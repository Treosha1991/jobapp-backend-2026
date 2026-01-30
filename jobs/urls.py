from django.urls import path
from .api import (
    VacancyListAPIView,
    VacancyDetailAPIView,
    VacancyCreateAPIView,
    VacancyContactAPIView,
)
from .auth_api import RegisterAPIView, LoginAPIView


urlpatterns = [
    path("auth/register/", RegisterAPIView.as_view(), name="api-register"),
    path("auth/login/", LoginAPIView.as_view(), name="api-login"),

    path("vacancies/", VacancyListAPIView.as_view(), name="vacancy-list"),
    path("vacancies/<int:pk>/", VacancyDetailAPIView.as_view(), name="vacancy-detail"),
    path("vacancies/create/", VacancyCreateAPIView.as_view(), name="vacancy-create"),
    path("vacancies/<int:pk>/contacts/", VacancyContactAPIView.as_view()),

]
