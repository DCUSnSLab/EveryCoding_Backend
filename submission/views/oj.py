import ipaddress
from git import Repo
import os

from django.conf import settings
from django.db.models import Q
from account.decorators import login_required, check_contest_permission
from contest.models import Contest, ContestStatus, ContestRuleType
from judge.dispatcher import JudgeDispatcher
from options.options import SysOptions
from problem.models import Problem, ProblemRuleType
from utils.api import APIView, validate_serializer
from utils.cache import cache
from utils.captcha import Captcha
from utils.throttling import TokenBucket
from ..models import Submission
from ..serializers import (CreateSubmissionSerializer, SubmissionModelSerializer,
                           ShareSubmissionSerializer)
from ..serializers import SubmissionSafeModelSerializer, SubmissionListSerializer


class SubmissionAPI(APIView):
    def throttling(self, request):
        # 使用 open_api 的请求暂不做限制
        auth_method = getattr(request, "auth_method", "")
        if auth_method == "api_key":
            return
        user_bucket = TokenBucket(key=str(request.user.id),
                                  redis_conn=cache, **SysOptions.throttling["user"])
        can_consume, wait = user_bucket.consume()
        if not can_consume:
            return "Please wait %d seconds" % (int(wait))

        # ip_bucket = TokenBucket(key=request.session["ip"],
        #                         redis_conn=cache, **SysOptions.throttling["ip"])
        # can_consume, wait = ip_bucket.consume()
        # if not can_consume:
        #     return "Captcha is required"

    @check_contest_permission(check_type="problems")
    def check_contest_permission(self, request):
        contest = self.contest
        if contest.status == ContestStatus.CONTEST_ENDED:
            return self.error("The contest have ended")
        if not request.user.is_contest_admin(contest):
            user_ip = ipaddress.ip_address(request.session.get("ip"))
            #user_ip = ipaddress.ip_address(request.data.get("ip"))
            if contest.allowed_ip_ranges:
                if not any(user_ip in ipaddress.ip_network(cidr, strict=False) for cidr in contest.allowed_ip_ranges):
                    return self.error("Your IP is not allowed in this contest")

    @validate_serializer(CreateSubmissionSerializer)
    @login_required
    def post(self, request):
        print("SubmissionAPI post")
        data = request.data
        hide_id = False
        if data.get("contest_id"):
            error = self.check_contest_permission(request)
            if error:
                return error
            contest = self.contest
            if not contest.problem_details_permission(request.user):
                hide_id = True

        if data.get("captcha"):
            if not Captcha(request).check(data["captcha"]):
                return self.error("Invalid captcha")
        error = self.throttling(request)
        if error:
            return self.error(error)

        try:
            problem = Problem.objects.get(id=data["problem_id"], contest_id=data.get("contest_id"), visible=True)
        except Problem.DoesNotExist:
            return self.error("Problem not exist")
        if data["language"] not in problem.languages:
            return self.error(f"언어 선택에 오류가 있습니다. 다시 확인해주세요. 선택된 언어 : {data['language']}")

        if data.get("contest_id"): # Contest에 소속된 문제인 경우,
            contest = Contest.objects.get(id=data.get("contest_id"))
            print(contest.title)
            print(contest.lecture_id)
            if contest.lecture_id is not None:
                submission = Submission.objects.create(user_id=request.user.id,
                                                       username=request.user.username,
                                                       language=data["language"],
                                                       code=data["code"],
                                                       problem_id=problem.id,
                                                       ip=request.session["ip"],
                                                       contest_id=data.get("contest_id"),
                                                       lecture_id=contest.lecture.id)
            else:
                submission = Submission.objects.create(user_id=request.user.id,
                                                       username=request.user.username,
                                                       language=data["language"],
                                                       code=data["code"],
                                                       problem_id=problem.id,
                                                       ip=request.session["ip"],
                                                       contest_id=data.get("contest_id"),
                                                       lecture_id=None)
        else: # 수강과목, 대회 어느 쪽에도 소속되지 않은 문제인 경우
            submission = Submission.objects.create(user_id=request.user.id,
                                                   username=request.user.username,
                                                   language=data["language"],
                                                   code=data["code"],
                                                   problem_id=problem.id,
                                                   ip=request.session["ip"],
                                                   contest_id=data.get("contest_id"),
                                                   lecture_id=None)
        # use this for debug
        JudgeDispatcher(submission.id, problem.id).judge()
        #judge_task.send(submission.id, problem.id)
        if hide_id:
            return self.success()
        else:
            # git clone from userid
            print("git clone")
            print(request.user.username)
            git_dir = os.path.join(settings.GIT_PATH, request.user.id)
            os.mkdir(git_dir,exist_ok=True)
            # os.makedirs('temp', exist_ok=True)
            repo = Repo.clone_from("https://github.com/"+ str(request.user.username) +"/EduCoder.git", git_dir)
            repo.git.checkout("master")
            print(repo)
            # make code file
            f = open("EduCoder/"+{problem.id}+".py", 'w')
            f.write(submission.code)
            f.close()
            # git add
            repo.git.add(A=True)
            # git commit
            repo.git.commit(m="commit")
            # git push
            repo.git.push()
            return self.success({"submission_id": submission.id})

    @login_required
    def get(self, request):
        submission_id = request.GET.get("id")
        if not submission_id:
            return self.error("Parameter id doesn't exist")
        try:
            submission = Submission.objects.select_related("problem").get(id=submission_id)
        except Submission.DoesNotExist:
            return self.error("Submission doesn't exist")
        if not submission.check_user_permission(request.user):
            return self.error("No permission for this submission")

        if submission.problem.rule_type == ProblemRuleType.OI or request.user.is_admin_role():
            submission_data = SubmissionModelSerializer(submission).data
        else:
            submission_data = SubmissionSafeModelSerializer(submission).data
        # 是否有权限取消共享
        submission_data["can_unshare"] = submission.check_user_permission(request.user, check_share=False)
        return self.success(submission_data)

    @validate_serializer(ShareSubmissionSerializer)
    @login_required
    def put(self, request):
        """
        share submission
        """
        try:
            submission = Submission.objects.select_related("problem").get(id=request.data["id"])
        except Submission.DoesNotExist:
            return self.error("Submission doesn't exist")
        if not submission.check_user_permission(request.user, check_share=False):
            return self.error("No permission to share the submission")
        if submission.contest and submission.contest.status == ContestStatus.CONTEST_UNDERWAY:
            return self.error("Can not share submission now")
        submission.shared = request.data["shared"]
        submission.save(update_fields=["shared"])
        return self.success()


class SubmissionLogAPI(APIView):
    def get(self, request):
        print("SubmissionLogAPI GET")
        contestID = request.GET.get("contestID")
        problemID = request.GET.get("problemID")
        if contestID:
            log = Submission.objects.filter(contest__id=contestID, problem___id=problemID, user=request.user)
            if log.exists():
                log = log.order_by('-create_time')[0]
                return self.success(SubmissionModelSerializer(log).data)
        elif problemID:
            log = Submission.objects.filter(problem__id=problemID, user=request.user)
            if log.exists():
                log = log.order_by('-create_time')[0]
                return self.success(SubmissionModelSerializer(log).data)
        return self.success()

class SubmissionListAPI(APIView):
    def get(self, request):
        print("SubmissionListAPI GET")
        if not request.GET.get("limit"):
            return self.error("Limit is needed")
        if request.GET.get("contest_id"):
            return self.error("Parameter error")

        submissions = Submission.objects.filter(contest_id__isnull=True).select_related("problem__created_by")
        problem_id = request.GET.get("problem_id")
        myself = request.GET.get("myself")
        result = request.GET.get("result")
        username = request.GET.get("username")
        if problem_id:
            try:
                problem = Problem.objects.get(_id=problem_id, contest_id__isnull=True, visible=True)
            except Problem.DoesNotExist:
                return self.error("Problem doesn't exist")
            submissions = submissions.filter(problem=problem)
        if (myself and myself == "1") or not SysOptions.submission_list_show_all:
            submissions = submissions.filter(user_id=request.user.id)
        elif username:
            # submissions = submissions.filter(username__icontains=username)
            # submissions = submissions.filter(user__realname__contains=username)
            submissions = submissions.filter(Q(user__realname__contains=username) | Q(username__icontains=username))
        if result:
            submissions = submissions.filter(result=result)
        data = self.paginate_data(request, submissions)
        data["results"] = SubmissionListSerializer(data["results"], many=True, user=request.user).data
        return self.success(data)


class ContestSubmissionListAPI(APIView):
    @check_contest_permission(check_type="submissions")
    def get(self, request):
        print("ContestSubmissionListAPI GET")
        if not request.GET.get("limit"):
            return self.error("Limit is needed")

        contest = self.contest
        submissions = Submission.objects.filter(contest_id=contest.id).select_related("problem__created_by").select_related("user")

        problem_id = request.GET.get("problem_id")
        myself = request.GET.get("myself")
        result = request.GET.get("result")
        username = request.GET.get("username")
        if problem_id:
            try:
                problem = Problem.objects.get(_id=problem_id, contest_id=contest.id, visible=True)
            except Problem.DoesNotExist:
                return self.error("Problem doesn't exist")
            submissions = submissions.filter(problem=problem)

        if myself and myself == "1":
            submissions = submissions.filter(user_id=request.user.id)
        elif username:
            submissions = submissions.filter(Q(user__realname__contains=username) | Q(username__icontains=username))
        if result:
            submissions = submissions.filter(result=result)

        # filter the test submissions submitted before contest start
        if contest.status != ContestStatus.CONTEST_NOT_START:
            submissions = submissions.filter(create_time__gte=contest.start_time)

        # 封榜的时候只能看到自己的提交
        if contest.rule_type == ContestRuleType.ACM:
            if not contest.real_time_rank and not request.user.is_contest_admin(contest):
                submissions = submissions.filter(user_id=request.user.id)

        data = self.paginate_data(request, submissions)
        data["results"] = SubmissionListSerializer(data["results"], many=True, user=request.user).data
        return self.success(data)


class SubmissionExistsAPI(APIView):
    def get(self, request):
        if not request.GET.get("problem_id"):
            return self.error("Parameter error, problem_id is required")
        return self.success(request.user.is_authenticated and
                            Submission.objects.filter(problem_id=request.GET["problem_id"],
                                                      user_id=request.user.id).exists())
