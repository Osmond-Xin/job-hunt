from job_hunt.services.board_queries import board_queries


def test_extra_queries_add_to_the_role_terms_without_duplicates() -> None:
    assert board_queries({"extra_queries": ["IT Analyst", "Data Analyst"]}, ["Data Analyst", " "]) == [
        "Data Analyst", "IT Analyst"]
    assert board_queries({"queries": ["Python Developer"], "extra_queries": ["IT Analyst"]}, ["AI Engineer"]) == [
        "Python Developer", "IT Analyst"]
    assert board_queries({}, []) == []
