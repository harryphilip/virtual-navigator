"""Great-circle helpers. Distances in nautical miles, bearings in degrees true."""
import math

EARTH_R_NM = 3440.065


def haversine_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_NM * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination(lat, lon, bearing, dist_nm):
    d = dist_nm / EARTH_R_NM
    b = math.radians(bearing)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), ((math.degrees(l2) + 540.0) % 360.0) - 180.0


def angle_diff(a, b):
    """Smallest absolute difference between two bearings, 0..180."""
    return abs(((a - b + 540.0) % 360.0) - 180.0)


def point_in_poly(lat, lon, pts):
    """Ray-cast point-in-polygon on plain lat/lon vertices [(lat, lon), ...].

    Edges are treated as straight lines in lat/lon space — the same shapes a
    race viewer draws — which is plenty for keep-out zones tens of miles
    across (and none of ours straddle the antimeridian).
    """
    inside = False
    n = len(pts)
    for i in range(n):
        la1, lo1 = pts[i][0], pts[i][1]
        la2, lo2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        if (la1 > lat) != (la2 > lat):
            x = lo1 + (lat - la1) * (lo2 - lo1) / (la2 - la1)
            if lon < x:
                inside = not inside
    return inside
