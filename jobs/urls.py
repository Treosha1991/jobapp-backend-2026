from django.urls import path
from .api import (
    VacancyListAPIView,
    VacancyDetailAPIView,
    VacancyCreateAPIView,
    VacancyPendingListAPIView,
    VacancyApproveAPIView,
    VacancyRejectAPIView,
    VacancyResubmitAPIView,
    VacancyMineAPIView,
    VacancyEditAPIView,
    VacancyContactAPIView,
    VacancyUnlockRequestAPIView,
    VacancyUnlockConfirmAPIView,
    ComplaintAPIView,
    ComplaintByVacancyAPIView,
    ComplaintListAPIView,
    ComplaintModerationActionAPIView,
)
from .auth_api import (
    RegisterAPIView,
    LoginAPIView,
    VerifyEmailAPIView,
    ResendCodeAPIView,
    ResetPasswordRequestAPIView,
    ResetPasswordConfirmAPIView,
    PhoneRequestCodeAPIView,
    PhoneVerifyCodeAPIView,
    MeAPIView,
    LinkEmailRequestAPIView,
    LinkEmailConfirmAPIView,
)


urlpatterns = [
    path("auth/register/", RegisterAPIView.as_view(), name="api-register"),
    path("auth/login/", LoginAPIView.as_view(), name="api-login"),
    path("auth/me/", MeAPIView.as_view(), name="api-me"),
    path("auth/verify/", VerifyEmailAPIView.as_view(), name="api-verify"),
    path("auth/resend/", ResendCodeAPIView.as_view(), name="api-resend"),
    path("auth/phone/request-code/", PhoneRequestCodeAPIView.as_view(), name="api-phone-request-code"),
    path("auth/phone/verify-code/", PhoneVerifyCodeAPIView.as_view(), name="api-phone-verify-code"),
    path("auth/link-email/request/", LinkEmailRequestAPIView.as_view(), name="api-link-email-request"),
    path("auth/link-email/confirm/", LinkEmailConfirmAPIView.as_view(), name="api-link-email-confirm"),
    path("auth/reset/request/", ResetPasswordRequestAPIView.as_view(), name="api-reset-request"),
    path("auth/reset/confirm/", ResetPasswordConfirmAPIView.as_view(), name="api-reset-confirm"),

    path("vacancies/", VacancyListAPIView.as_view(), name="vacancy-list"),
    path("vacancies/<int:pk>/", VacancyDetailAPIView.as_view(), name="vacancy-detail"),
    path("vacancies/create/", VacancyCreateAPIView.as_view(), name="vacancy-create"),
    path("vacancies/mine/", VacancyMineAPIView.as_view(), name="vacancy-mine"),
    path("vacancies/<int:pk>/edit/", VacancyEditAPIView.as_view(), name="vacancy-edit"),
    path("vacancies/pending/", VacancyPendingListAPIView.as_view(), name="vacancy-pending"),
    path("vacancies/<int:pk>/approve/", VacancyApproveAPIView.as_view(), name="vacancy-approve"),
    path("vacancies/<int:pk>/reject/", VacancyRejectAPIView.as_view(), name="vacancy-reject"),
    path("vacancies/<int:pk>/resubmit/", VacancyResubmitAPIView.as_view(), name="vacancy-resubmit"),

    path("vacancies/<int:pk>/contacts/", VacancyContactAPIView.as_view()),

    path("vacancies/<int:pk>/unlock/request/", VacancyUnlockRequestAPIView.as_view(), name="vacancy-unlock-request"),
    path("vacancies/<int:pk>/unlock/confirm/", VacancyUnlockConfirmAPIView.as_view(), name="vacancy-unlock-confirm"),
    path("complaints/", ComplaintAPIView.as_view(), name="complaints"),
    path("moderation/complaints/", ComplaintListAPIView.as_view(), name="complaints-list"),
    path("moderation/complaints/by-vacancy/", ComplaintByVacancyAPIView.as_view(), name="complaints-by-vacancy"),
    path("moderation/complaints/<int:pk>/action/", ComplaintModerationActionAPIView.as_view(), name="complaint-action"),


]
