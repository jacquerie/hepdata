# -*- coding: utf-8 -*-
#
# This file is part of HEPData.
# Copyright (C) 2016 CERN.
#
# HEPData is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# HEPData is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HEPData; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, CERN does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

"""HEPData CLI module."""

from __future__ import absolute_import, print_function

import click
from flask.cli import with_appcontext
from invenio_base.app import create_cli

from hepdata.ext.elasticsearch.admin_view.api import AdminIndexer
from hepdata.modules.converter.tasks import convert_and_store
from hepdata.modules.records.utils.common import record_exists
from hepdata.modules.submission.models import HEPSubmission
from .factory import create_app
from hepdata.config import CFG_PUB_TYPE
from hepdata.ext.elasticsearch.api import reindex_all, get_records_matching_field
from hepdata.modules.records.utils.submission import unload_submission
from hepdata.modules.records.migrator.api import load_files, update_submissions, get_all_ids_in_current_system, \
    add_or_update_records_since_date, update_analyses

cli = create_cli(create_app=create_app)

default_recids = 'ins1283842,ins1245023'


@cli.command()
@with_appcontext
@click.option('--inspireids', '-i', default=default_recids,
              help='A comma separated list of recids to load.')
@click.option('--recreate_index', '-rc', default=True, type=bool,
              help='Whether or not to recreate the index')
@click.option('--tweet', '-t', default=False, type=bool,
              help='Whether or not to send a tweet announcing the arrival of these records.')
@click.option('--convert', '-c', default=False, type=bool,
              help='Whether or not to create conversions for all loaded files to ROOT, YODA, and CSV.')
def populate(inspireids, recreate_index, tweet, convert):
    """Populate the DB with records.
        Usage: hepdata populate -i 'ins1262703' -rc False -t False
        :param convert:
        :param inspireids:
        :param tweet:
        :param recreate_index:

    """
    from hepdata.ext.elasticsearch.api import recreate_index as reindex

    if recreate_index:
        reindex()

    files_to_load = parse_inspireids_from_string(inspireids)
    load_files(files_to_load, send_tweet=tweet, convert=convert)


@cli.command()
@with_appcontext
@click.option('--start', '-s', type=int, default=None,
              help='The start index from the total inspireids to load.')
@click.option('--end', '-e', default=None, type=int,
              help='The end index from the total inspireids to load.')
@click.option('--date', '-d', type=str, default=None,
              help='Filter all records modified since some point in time, e.g. 20160705 for the 5th July 2016.')
@click.option('--missing-only', '-m', default=False,
              type=bool,
              help='This option will automatically find the inspire ids in the current '
                   'hepdata but not in this version and migrate them.')
def migrate(start, end, date=None, missing_only=False):
    """
    Migrates all content from HEPData
    :return:
    """
    if missing_only:
        inspire_ids = get_missing_records()
    else:
        inspire_ids = get_all_ids_in_current_system(date)

    print("Found {} inspire ids to load.".format(len(inspire_ids)))
    if start is not None:
        _slice = slice(int(start), end)
        inspire_ids = inspire_ids[_slice]
        print("Sliced, going to load {} records.".format(len(inspire_ids)))
        print(inspire_ids)

    load_files(inspire_ids)


@cli.command()
@with_appcontext
@click.option('--date', '-d', type=str, default=None, help='Date in the format YYYYddMM, e.g 20160627')
def add_or_update(date):
    add_or_update_records_since_date.delay(date)


@cli.command()
@with_appcontext
@click.option('--inspireids', '-i',
              help='A comma separated list of recids to load.')
@click.option('--update_record_info_only', '-ro', default=False, type=bool,
              help='True if you just want to update the publication information.')
def update(inspireids, update_record_info_only):
    """
    Given a list of record ids, can update the contents of the whole submission, or just the record information
    via the update_record_info_only option.
    Usage: hepdata update -i 'insXXX' -ro True|False
    :param inspireids: comma separated list of inspire ids, e.g. ins222121
    :param update_record_info_only: if True, will only up the record information, and won't update the data files.
    :return:
    """
    records_to_update = parse_inspireids_from_string(inspireids)
    update_submissions.delay(records_to_update, update_record_info_only)


@cli.command()
@with_appcontext
def find_duplicates_and_remove():
    """
    Will go through the application to find any duplicates then remove them.
    :return:
    """
    inspire_ids = get_all_ids_in_current_system(prepend_id_with="")

    duplicates = []
    for inspire_id in inspire_ids:
        matches = get_records_matching_field('inspire_id', inspire_id,
                                             doc_type=CFG_PUB_TYPE)
        if len(matches['hits']['hits']) > 1:
            duplicates.append(matches['hits']['hits'][0]['_source']['recid'])
    print('There are {} duplicates. Going to remove.'.format(len(duplicates)))
    do_unload(duplicates)

    # reindex submissions for dashboard view
    admin_indexer = AdminIndexer()
    admin_indexer.reindex(recreate=True)


@cli.command()
@with_appcontext
def get_missing_records():
    """
    Finds all records that are missing in the new system (compared to the legacy environment)
    and returns the IDs as a list
    :return: an array of missing IDd
    """
    inspire_ids = get_all_ids_in_current_system(prepend_id_with="")
    missing_ids = []
    for inspire_id in inspire_ids:
        if not record_exists(inspire_id=inspire_id):
            missing_ids.append(inspire_id)

    print("Missing {} records.".format(len(missing_ids)))
    print(missing_ids)
    return missing_ids


@cli.command()
@with_appcontext
@click.option('--recreate', '-rc', type=bool, default=False,
              help='Whether or not to recreate the index mappings as well. '
                   'This DELETES the entire index first.')
@click.option('--start', '-s', type=int, default=-1,
              help='Starting recid for the index operation.')
@click.option('--end', '-e', type=int, default=-1,
              help='End recid for the index operation.')
@click.option('--batch', '-b', type=int, default=50,
              help='Number of records to index at a time.')
def reindex(recreate, start, end, batch):
    reindex_all(recreate=recreate, start=start, end=end, batch=batch)


@cli.command()
@with_appcontext
@click.option('--inspireids', '-i', type=str,
              help='Load specific recids in to the system.')
@click.option('--tweet', '-t', default=False, type=bool,
              help='Whether or not to send a tweet announcing the arrival of these records.')
@click.option('--convert', '-c', default=False, type=bool,
              help='Whether or not to create conversions for all loaded files to ROOT, YODA, and CSV.')
def load(inspireids, tweet, convert):
    """
    Remove records given their HEPData IDs from the database.
    Removes all database entries, leaves the files on the server.
    :param convert:
    :param tweet:
    :param inspireids: list of record IDs to load
    """
    processed_record_ids = parse_inspireids_from_string(inspireids)

    load_files(processed_record_ids, send_tweet=tweet, convert=convert)


def parse_inspireids_from_string(records_to_unload):
    processed_record_ids = []
    records = records_to_unload.split(',')
    for record_id in records:
        processed_record_ids.append(record_id.strip())
    return processed_record_ids


@cli.command()
@with_appcontext
@click.option('--recids', '-r', type=str,
              help='Unload specific recids in to the system.')
def unload(recids):
    """
    Remove records given their HEPData IDs from the database.
    Removes all database entries, leaves the files on the server.
    :param record_ids: list of record IDs to remove
    :return:
    """
    records_to_unload = recids.split(',')

    processed_record_ids = []
    for record_id in records_to_unload:
        processed_record_ids.append(int(record_id.strip()))

    do_unload(processed_record_ids)


def do_unload(records_to_unload):
    for record_id in records_to_unload:
        unload_submission(record_id)


@cli.command()
@with_appcontext
def find_and_add_record_analyses():
    update_analyses.delay()


@cli.command()
@click.option('--inspire_ids', '-i', type=str,
              help='Specify inspire ids of submissions to generate the cached files for.')
@click.option('--force', '-f', type=bool, default=False,
              help='Force re-creation of converted files.')
@click.option('--targets', '-t', type=str, default='root,csv,yoda',
              help='Force re-creation of converted files.')
def prefetch_converted_files(inspire_ids, force, targets):
    """
    Goes through all HEPData submissions and creates their ROOT, CSV, and YODA representations.
    This avoids any wait time for users when trying to retrieve converted files.
    NOTE: Does not pre-fetch all individual files, since this would be too much and probably not
    necessary
    :return:
    """
    if inspire_ids:
        submission_ids = inspire_ids.split(',')
    else:
        submissions = HEPSubmission.query.filter_by(overall_status='finished') \
            .with_entities(HEPSubmission.inspire_id).all()
        submission_ids = [i for (i,) in submissions]

    submission_ids = set(submission_ids)

    file_formats = targets.split(',')
    for inspire_id in submission_ids:
        for file_format in file_formats:
            convert_and_store.delay(inspire_id, file_format, force=force)


@cli.group()
def submissions():
    """Submissions"""


@submissions.command(name="reindex")
@with_appcontext
def reindex():
    """Reindexes HEPSubmissions and adds to the submission index"""

    admin_idx = AdminIndexer()
    admin_idx.reindex(recreate=True)
