from django.shortcuts import render
from django.http import JsonResponse
from django.conf import settings
import requests
import math


def get_tomtom_key(request):
    """Frontend ko API key bhejo"""
    return JsonResponse({'key': settings.TOMTOM_API_KEY})


def geocode_location(request):
    """City name → lat/lng coordinates"""
    query = request.GET.get('q', '').strip()

    if not query or len(query) < 2:
        return JsonResponse({'error': 'Query too short'}, status=400)

    key = settings.TOMTOM_API_KEY
    url = f'https://api.tomtom.com/search/2/geocode/{query}.json'
    params = {'key': key, 'limit': 1, 'countrySet': 'US'}

    try:
        r = requests.get(url, params=params)
        data = r.json()

        if 'results' in data and data['results']:
            pos = data['results'][0]['position']
            return JsonResponse({
                'lat': pos['lat'],
                'lng': pos['lon'],
                'name': data['results'][0]['address']['freeformAddress']
            })

        return JsonResponse({'error': 'Location not found'}, status=404)

    except Exception as e:
        return JsonResponse({'error': f"Backend Error: {str(e)}"}, status=500)


def get_route(request):
    """3 locations → real road route with geometry"""
    origin  = request.GET.get('origin')
    pickup  = request.GET.get('pickup')
    dropoff = request.GET.get('dropoff')

    if not all([origin, pickup, dropoff]):
        return JsonResponse({'error': 'Missing location parameters'}, status=400)

    key = settings.TOMTOM_API_KEY
    locations = f"{origin}:{pickup}:{dropoff}"
    url = f'https://api.tomtom.com/routing/1/calculateRoute/{locations}/json'
    params = {
        'key': key,
        'routeType': 'fastest',
        'traffic': 'true',
        'travelMode': 'truck',
        'vehicleCommercial': 'true',
    }

    try:
        r = requests.get(url, params=params)
        data = r.json()

        if 'routes' not in data or not data['routes']:
            return JsonResponse({'error': 'Route not found by TomTom'}, status=404)

        route = data['routes'][0]
        legs  = route['legs']

        points = []
        for leg in legs:
            for point in leg['points']:
                points.append({'lat': point['latitude'], 'lng': point['longitude']})

        summary = route['summary']

        return JsonResponse({
            'points': points,
            'distanceMeters': summary['lengthInMeters'],
            'distanceMiles': round(summary['lengthInMeters'] * 0.000621371, 1),
            'travelTimeSeconds': summary['travelTimeInSeconds'],
            'legs': [
                {
                    'distanceMiles': round(leg['summary']['lengthInMeters'] * 0.000621371, 1),
                    'travelTimeSeconds': leg['summary']['travelTimeInSeconds']
                }
                for leg in legs
            ]
        })

    except Exception as e:
        return JsonResponse({'error': f"Routing Error: {str(e)}"}, status=500)


# ── HOS Calculation — moved from React to Django ──
import math
from django.http import JsonResponse

def calculate_trip(request):
    """
    Full FMCSA-compliant HOS trip calculation.
    Returns timeline events with start/end hours for ELD graph rendering.
    """
    try:
        import math

        leg1_miles = float(request.GET.get('leg1Miles', 0))
        leg2_miles = float(request.GET.get('leg2Miles', 0))
        cycle_used = float(request.GET.get('cycleUsed', 0))
        hos_rule = request.GET.get('hosRule', '70')

        current_location = request.GET.get('currentLocation', 'Origin')
        pickup_location = request.GET.get('pickupLocation', 'Pickup')
        dropoff_location = request.GET.get('dropoffLocation', 'Dropoff')

        # ─────────────────────────────────────────────
        # FMCSA Constants
        # ─────────────────────────────────────────────

        SPEED_MPH = 55
        MAX_DRIVE_DAY = 11
        BREAK_AFTER = 8
        FUEL_EVERY = 1000

        MAX_CYCLE = 70 if hos_rule == "70" else 60
        CYCLE_DAYS = 8 if hos_rule == "70" else 7

        total_miles = leg1_miles + leg2_miles

        # ─────────────────────────────────────────────
        # State
        # ─────────────────────────────────────────────

        timeline = []

        day_num = 1
        clock = 6.0

        drive_today = 0.0
        continuous_drive = 0.0

        fuel_mile_accum = 0.0
        total_drive_used = 0.0

        # ─────────────────────────────────────────────
        # Helpers
        # ─────────────────────────────────────────────

        def fmt_time(h):
            hh = int(h) % 24
            mm = round((h % 1) * 60)

            if mm == 60:
                hh = (hh + 1) % 24
                mm = 0

            ap = "PM" if hh >= 12 else "AM"

            return f"{hh % 12 or 12}:{mm:02d} {ap}"

        def add_event(
            name,
            typ,
            meta,
            start=None,
            end=None
        ):
            timeline.append({
                "name": name,
                "type": typ,
                "meta": meta,
                "day": day_num,
                "start": round(start, 2) if start is not None else None,
                "end": round(end, 2) if end is not None else None
            })

        # ─────────────────────────────────────────────
        # Trip Start
        # ─────────────────────────────────────────────

        add_event(
            current_location,
            "origin",
            f"Day 1 · 06:00 AM"
        )

        schedule_legs = [
            {
                "miles": leg1_miles,
                "dest": pickup_location,
                "type": "pickup",
                "stop": 1,
                "label": f"Drive to {pickup_location}"
            },
            {
                "miles": leg2_miles,
                "dest": dropoff_location,
                "type": "dropoff",
                "stop": 1,
                "label": f"Drive to {dropoff_location}"
            }
        ]

        # ─────────────────────────────────────────────
        # Main Simulation
        # ─────────────────────────────────────────────

        for leg in schedule_legs:

            leg_miles_left = leg["miles"]

            while leg_miles_left > 0:

                # Fuel Stop

                if fuel_mile_accum >= FUEL_EVERY:

                    fuel_start = clock
                    fuel_end = clock + 0.3

                    add_event(
                        "Fuel Stop",
                        "fuel",
                        f"Day {day_num} · {fmt_time(fuel_start)} · 0.3h",
                        start=fuel_start,
                        end=fuel_end
                    )

                    clock = fuel_end
                    fuel_mile_accum = 0.0

                # 30 Min Break

                if continuous_drive >= BREAK_AFTER:

                    break_start = clock
                    break_end = clock + 0.5

                    add_event(
                        "30-min Mandatory Break",
                        "break30",
                        f"Day {day_num} · {fmt_time(break_start)}",
                        start=break_start,
                        end=break_end
                    )

                    clock = break_end
                    continuous_drive = 0.0

                # Daily Limit

                if drive_today >= MAX_DRIVE_DAY and leg_miles_left > 0:

                    rest_start = clock

                    add_event(
                        "10h Off-Duty Rest",
                        "rest",
                        f"Day {day_num} · {fmt_time(rest_start)}",
                        start=rest_start,
                        end=min(24, rest_start + 10)
                    )

                    day_num += 1

                    clock = 6.0
                    drive_today = 0.0
                    continuous_drive = 0.0

                    add_event(
                        f"Resume Drive — Day {day_num}",
                        "origin",
                        f"Day {day_num} · 06:00 AM"
                    )

                    continue

                can_drive = min(
                    BREAK_AFTER - continuous_drive,
                    MAX_DRIVE_DAY - drive_today
                )

                hrs_to_leg = leg_miles_left / SPEED_MPH

                drive_now = min(
                    can_drive,
                    hrs_to_leg
                )

                if drive_now <= 0:
                    break

                miles_now = drive_now * SPEED_MPH

                drive_start = clock
                drive_end = clock + drive_now

                add_event(
                    leg["label"],
                    "driving",
                    f"Day {day_num} · {fmt_time(drive_start)} → {fmt_time(drive_end)} · {round(miles_now,1)} mi",
                    start=drive_start,
                    end=drive_end
                )

                clock = drive_end

                drive_today += drive_now
                continuous_drive += drive_now
                total_drive_used += drive_now

                leg_miles_left -= miles_now
                fuel_mile_accum += miles_now

                if leg_miles_left < 0.01:
                    leg_miles_left = 0

            # Arrival

            stop_start = clock
            stop_end = clock + leg["stop"]

            add_event(
                leg["dest"],
                leg["type"],
                f"Day {day_num} · {fmt_time(clock)} · {leg['stop']}h stop",
                start=stop_start,
                end=stop_end
            )

            clock = stop_end
            continuous_drive = 0.0

            # Pickup Late Day

            if (
                leg["type"] == "pickup"
                and clock > 16
                and leg != schedule_legs[-1]
            ):

                rest_start = clock

                add_event(
                    "10h Off-Duty Rest",
                    "rest",
                    f"Day {day_num} · {fmt_time(rest_start)}",
                    start=rest_start,
                    end=min(24, rest_start + 10)
                )

                day_num += 1

                clock = 6.0
                drive_today = 0.0
                continuous_drive = 0.0

                add_event(
                    f"Resume Drive — Day {day_num}",
                    "origin",
                    f"Day {day_num} · 06:00 AM"
                )

        # ─────────────────────────────────────────────
        # Summary
        # ─────────────────────────────────────────────

        fuel_stops = len([
            e for e in timeline
            if e["type"] == "fuel"
        ])

        hos_violation = (
            cycle_used + total_drive_used
        ) > MAX_CYCLE

        hours_needed = math.ceil(total_drive_used)

        hours_avail = max(
            0,
            MAX_CYCLE - cycle_used
        )

        return JsonResponse({
            "totalMiles": round(total_miles, 1),
            "daysNeeded": day_num,
            "fuelStops": fuel_stops,
            "hoursAvail": hours_avail,
            "totalDriveHrs": round(total_drive_used, 1),
            "timeline": timeline,
            "hosViolation": hos_violation,
            "hosViolationMsg": (
                f"This trip requires ~{hours_needed}h of drive time, "
                f"but you only have {hours_avail}h left in your "
                f"{MAX_CYCLE}h/{CYCLE_DAYS}-day cycle. "
                f"Consider a 34-hour restart."
            ) if hos_violation else None,
            "maxCycle": MAX_CYCLE,
            "cycleUsed": cycle_used
        })

    except Exception as e:
        return JsonResponse(
            {"error": f"Calculation Error: {str(e)}"},
            status=500
        )
