from django.urls import path
from . import views

urlpatterns = [
    path('api/tomtom-key/', views.get_tomtom_key),
    path('api/geocode/', views.geocode_location),
    path('api/route/', views.get_route),
    path('api/calculate-trip/', views.calculate_trip),
]