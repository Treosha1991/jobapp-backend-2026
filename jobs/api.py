from django.utils import timezone
from rest_framework import generics, permissions

from .models import Vacancy
from .serializers import (
    VacancyListSerializer,
    VacancyDetailSerializer,
    VacancyCreateSerializer,
)
from datetime import timedelta
from django.utils import timezone

class VacancyListAPIView(generics.ListAPIView):
    serializer_class = VacancyListSerializer

    def get_queryset(self):
        qs = Vacancy.objects.filter(
            is_approved=True,
            expires_at__gt=timezone.now()
        ).order_by("-published_at")

        country = self.request.query_params.get("country")
        city = self.request.query_params.get("city")
        category = self.request.query_params.get("category")
        employment_type = self.request.query_params.get("employment_type")
        source = self.request.query_params.get("source")

        if country:
            qs = qs.filter(country=country)
        if city:
            qs = qs.filter(city__icontains=city)
        if category:
            qs = qs.filter(category=category)
        if employment_type:
            qs = qs.filter(employment_type=employment_type)
        if source:
            qs = qs.filter(source=source)

        return qs


class VacancyDetailAPIView(generics.RetrieveAPIView):
    serializer_class = VacancyDetailSerializer

    def get_queryset(self):
        return Vacancy.objects.filter(is_approved=True, expires_at__gt=timezone.now())


class VacancyCreateAPIView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = VacancyCreateSerializer

    def perform_create(self, serializer):
        serializer.save(
            created_by=self.request.user,
            is_approved=False,
            expires_at=timezone.now() + timedelta(days=30),
        )

from rest_framework.response import Response
from rest_framework.views import APIView
from .models import UnlockedContact
from .serializers import VacancyContactSerializer

class VacancyContactAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)

        unlocked = UnlockedContact.objects.filter(
            user=request.user,
            vacancy=vacancy
        ).exists()

        if not unlocked:
            return Response(
                {"detail": "contacts_locked"},
                status=403
            )

        serializer = VacancyContactSerializer(vacancy)
        return Response(serializer.data)

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)

        UnlockedContact.objects.get_or_create(
            user=request.user,
            vacancy=vacancy
        )

        serializer = VacancyContactSerializer(vacancy)
        return Response(serializer.data)
    
from .models import UnlockRequest
from rest_framework import status


class VacancyUnlockRequestAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)

        # если уже открыто — сразу вернём "already_unlocked"
        if UnlockedContact.objects.filter(user=request.user, vacancy=vacancy).exists():
            return Response({"detail": "already_unlocked"}, status=200)

        unlock = UnlockRequest.create_for(request.user, vacancy)
        return Response(
            {
                "unlock_token": unlock.token,
                "expires_in_seconds": 120
            },
            status=200
        )


class VacancyUnlockConfirmAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        token = request.data.get("unlock_token")

        if not token:
            return Response({"error": "unlock_token required"}, status=status.HTTP_400_BAD_REQUEST)

        qs = UnlockRequest.objects.filter(
            user=request.user,
            vacancy=vacancy,
            token=token
        ).order_by("-created_at")

        if not qs.exists():
            return Response({"error": "invalid token"}, status=status.HTTP_400_BAD_REQUEST)

        unlock_req = qs.first()
        if not unlock_req.is_valid():
            return Response({"error": "token expired"}, status=status.HTTP_400_BAD_REQUEST)

        UnlockedContact.objects.get_or_create(user=request.user, vacancy=vacancy)

        # токен можно удалить, чтобы нельзя было использовать повторно
        unlock_req.delete()

        serializer = VacancyContactSerializer(vacancy)
        return Response(serializer.data, status=200)


