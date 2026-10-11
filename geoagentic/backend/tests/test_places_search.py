from app.places import search


def test_search_matches_words_in_any_order_and_ignores_fillers():
    results = search("hospital near Koramangala")

    assert any(place["name"] == "St. John's Medical College Hospital" for place in results)


def test_search_handles_small_typos_and_bangalore_alias():
    results = search("bangalore palce")

    assert results[0]["name"] == "Bangalore Palace"


def test_search_ranks_name_matches_ahead_of_area_matches():
    results = search("cubbon park")

    assert results[0]["name"] == "Cubbon Park"
