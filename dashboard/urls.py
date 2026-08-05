from django.urls import path
from .views import DashboardCardsAPIView, DashboardChartsAPIView

urlpatterns = [
    path('cards/', DashboardCardsAPIView.as_view(), name='dashboard-cards'),
    path('charts/', DashboardChartsAPIView.as_view(), name='dashboard-charts'),
]
