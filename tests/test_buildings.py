from radar.buildings import in_building, name_matches
from radar.models import Listing


def listing(title="", building="", deal="rent", lat=None, lon=None):
    return Listing(source="s", source_id=title, deal=deal, title=title, url="u", building=building, lat=lat, lon=lon)


def test_name_matching_tolerates_site_spellings():
    assert name_matches("Prestige Lakeside Habitat", listing(building="Prestige Lakeside Habitat Phase 2"))
    assert name_matches("Prestige Lakeside Habitat", listing("3 BHK Flat for Rent in Prestige Lakeside Habitat, Whitefield"))
    assert name_matches("DNR Spring Leaf", listing("3 BHK Flat In Dnr Springleaf for Rent In Koramangala"))
    assert name_matches("Lake Side Residency", listing(building="Lakeside Residency"))
    assert name_matches("Sobha City Apartments", listing("2 BHK in Sobha City Mykonos"))       # generic word ignored
    assert name_matches("Brigade Gateway", listing(building="Brigade Gate way"))
    assert name_matches("Mantri Serenity", listing(building="Mantri Serinity"))                  # typo, fuzzy match


def test_name_matching_rejects_other_buildings():
    assert not name_matches("Prestige Lakeside Habitat", listing(building="Prestige Shantiniketan"))
    assert not name_matches("Mantri Classic", listing("1 BHK in Mantri Serenity"))
    assert not name_matches("Sobha City", listing("2 BHK Flat for Rent in Koramangala"))


def test_in_building_checks_deal_and_distance():
    b = {"name": "Mantri Classic", "deal": "rent", "lat": 12.9300, "lon": 77.6300, "radius_km": 1}
    assert in_building(b, listing("Flat in Mantri Classic", lat=12.931, lon=77.631))
    assert in_building(b, listing("Flat in Mantri Classic"))                      # no coordinates: name decides
    assert not in_building(b, listing("Flat in Mantri Classic", lat=13.05, lon=77.63))   # same name, 13 km away
    assert not in_building(b, listing("Flat in Mantri Classic", deal="sale"))
    assert in_building({**b, "deal": "both"}, listing("Flat in Mantri Classic", deal="sale"))
