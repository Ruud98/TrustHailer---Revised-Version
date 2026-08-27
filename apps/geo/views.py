from django.http import JsonResponse
from django.shortcuts import render

from .models import City, Suburb


def suburb_options(request):
    """
    HTMX partial: returns <option> tags for a city's suburbs.
    Used by the dependent City -> Suburb selects during onboarding.
    """
    city_id = request.GET.get("city")
    suburbs = Suburb.objects.none()
    if city_id and str(city_id).isdigit():
        suburbs = Suburb.objects.filter(city_id=city_id).order_by("name")
    return render(request, "geo/_suburb_options.html", {"suburbs": suburbs})


def suburb_search(request):
    """Typeahead endpoint. Returns at most 15 matches."""
    q = (request.GET.get("q") or "").strip()
    results = []
    if len(q) >= 2:
        qs = (
            Suburb.objects.select_related("city")
            .filter(name__istartswith=q)
            .order_by("name")[:15]
        )
        results = [{"id": s.pk, "label": s.full_name} for s in qs]
    return JsonResponse({"results": results})
