from __future__ import absolute_import, print_function

from flask import Blueprint, jsonify, request, render_template
from flask.ext.login import login_required, current_user
from hepdata.ext.elasticsearch.api import reindex_all
from hepdata.ext.elasticsearch.api import push_data_keywords
from hepdata.modules.dashboard.api import prepare_submissions, get_pending_invitations_for_user
from hepdata.modules.permissions.api import get_pending_request, get_pending_coordinator_requests
from hepdata.modules.permissions.views import check_is_sandbox_record
from hepdata.modules.records.utils.submission import unload_submission, do_finalise
from hepdata.modules.records.utils.users import has_role
import json

from invenio_userprofiles import current_userprofile

__author__ = 'eamonnmaguire'

blueprint = Blueprint('hep_dashboard', __name__, url_prefix="/dashboard",
                      template_folder='templates',
                      static_folder='static')


@blueprint.route('/')
@login_required
def dashboard():
    """
        Depending on the user that is logged in, they will get a
        dashboard that reflects the
        current status of all submissions of which they are part.
    """

    submissions = prepare_submissions(current_user)

    submission_meta = []
    submission_stats = []

    for record_id in submissions:
        stats = []

        for key in submissions[record_id]["stats"].keys():
            stats.append(
                {"name": key, "count": submissions[record_id]["stats"][key]})

        submission_stats.append({"recid": record_id, "stats": stats})

        review_flag = "todo"
        if submissions[record_id]["stats"]["attention"] == 0 and \
                submissions[record_id]["stats"]["todo"] == 0 and \
                submissions[record_id]["stats"]["passed"] == 0:
            review_flag = "todo"
        elif submissions[record_id]["stats"]["attention"] > 0 or \
                submissions[record_id]["stats"]["todo"] > 0:
            review_flag = "attention"
        elif submissions[record_id]["stats"]["attention"] == 0 and \
                submissions[record_id]["stats"]["todo"] == 0:
            review_flag = "passed"

        if submissions[record_id]["status"] == 'finished':
            review_flag = "finished"

        submissions[record_id]["metadata"]["submission_status"] = \
            submissions[record_id]["status"]
        submissions[record_id]["metadata"]["review_flag"] = review_flag

        submission_meta.append(submissions[record_id]["metadata"])

    user_profile = current_userprofile.query.filter_by(user_id=current_user.get_id()).first()

    ctx = {'user_is_admin': has_role(current_user, 'admin'),
           'submissions': submission_meta,
           'user_profile': user_profile,
           'user_has_coordinator_request': get_pending_request(),
           'pending_coordinator_requests': get_pending_coordinator_requests(),
           'submission_stats': json.dumps(submission_stats),
           'pending_invites': get_pending_invitations_for_user(current_user)}

    return render_template('hepdata_dashboard/dashboard.html', ctx=ctx)


@blueprint.route('/delete/<int:recid>')
@login_required
def delete_submission(recid):
    """
    Submissions can only be removed if they are not finalised,
    meaning they should never be in the index.
    :param recid:
    :return:
    """
    if has_role(current_user, 'admin') or has_role(current_user, 'coordinator') \
        or check_is_sandbox_record(recid):
        unload_submission(recid)
        return json.dumps({"success": True,
                           "recid": recid,
                           "errors": [
                               "Record successfully removed!"]})
    else:
        return json.dumps(
            {"success": False, "recid": recid,
             "errors": [
                 "You do not have permission to delete this submission. "
                 "Only coordinators can do that."]})


@blueprint.route('/manage/reindex/', methods=['POST'])
@login_required
def reindex():
    if has_role(current_user, 'admin'):
        reindex_all(recreate=True)
        push_data_keywords()
        return jsonify({"success": True})
    else:
        return jsonify({"success": False,
                        'message': "You don't have sufficient privileges to "
                                   "perform this action."})


@blueprint.route('/finalise/<int:recid>', methods=['POST'])
@login_required
def finalise(recid, publication_record=None, force_finalise=False):
    commit_message = request.form.get('message')

    return do_finalise(recid, publication_record, force_finalise,
                       commit_message=commit_message, send_tweet=True)
