from django.urls import path

from . import web_views

app_name = "employer"

urlpatterns = [
    path("", web_views.vacancy_list, name="vacancy_list"),
    path("login/", web_views.EmployerLoginView.as_view(), name="login"),
    path("password-reset/", web_views.password_reset, name="password_reset"),
    path("chats/", web_views.chat_list, name="chat_list"),
    path("chats/<int:conversation_id>/", web_views.chat_detail, name="chat_detail"),
    path("language/<str:lang>/", web_views.set_language, name="set_language"),
    path("logout/", web_views.EmployerLogoutView.as_view(), name="logout"),
    path("phone/request-code/", web_views.phone_request_code, name="phone_request_code"),
    path("phone/verify-code/", web_views.phone_verify_code, name="phone_verify_code"),
    path("vacancies/new/", web_views.vacancy_create, name="vacancy_create"),
    path("vacancies/<int:pk>/edit/", web_views.vacancy_edit, name="vacancy_edit"),
    path("vacancies/<int:pk>/pause/", web_views.vacancy_pause, name="vacancy_pause"),
    path("vacancies/<int:pk>/delete/", web_views.vacancy_delete, name="vacancy_delete"),
]
