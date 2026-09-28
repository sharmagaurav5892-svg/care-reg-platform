"""Silver parser: BC Laws XML -> units. Uses a made-up regulation shaped like the real files."""
from pathlib import Path

from careplatform.silver import bclaws_xml

SAMPLE = (Path(__file__).parent / "fixtures" / "bc_regulation_sample.xml").read_bytes()


def units_by_ref():
    return {u.unit_ref: u for u in bclaws_xml.parse(SAMPLE)}


def test_sections_get_citation_refs_and_context():
    u = units_by_ref()
    assert list(u) == ["s. 1", "s. 12", "s. 13", "s. 14", "s. 15", "s. 16", "s. 30-31", "Sch. A, s. 1"]
    assert u["s. 13"].heading == "Fire drills"
    assert u["s. 13"].context_path == "Part 2 Care and Supervision > Division 1 Safety"


def test_schedule_sections_do_not_clash_with_body_sections():
    u = units_by_ref()
    assert "s. 1" in u and "Sch. A, s. 1" in u


def test_repealed_subsection_is_dropped_but_rest_of_section_kept():
    s12 = units_by_ref()["s. 12"]
    assert not s12.is_repealed
    assert "Repealed" not in s12.text
    assert s12.text.startswith("(2) A licensee must cooperate")
    assert "(a) giving access" in s12.text
    assert "(1) Repealed" in s12.history_note


def test_whole_repealed_section_is_flagged():
    assert units_by_ref()["s. 30-31"].is_repealed


def test_table_rows_stay_together():
    text = units_by_ref()["s. 14"].text
    assert "Item: 1 | Persons in care: 1 to 10 | Bathing facilities: 1" in text


def test_consequential_amendments_table_is_skipped():
    assert not any("Affected Act" in u.text for u in bclaws_xml.parse(SAMPLE))


def test_inline_links_become_cross_references():
    s1 = units_by_ref()["s. 1"]
    assert s1.links == [("/legislation/02075_01", "Community Care and Assisted Living Act")]
    assert bclaws_xml.target_doc_id("/legislation/02075_01") == "02075_01"
    assert '"care facility" means' in s1.text          # inline tags joined without stray spaces


def test_history_note_kept():
    assert units_by_ref()["s. 13"].history_note == "[en. B.C. Reg. 192/2022, Sch. 4, s. 2.]"


def test_not_in_force_section_is_flagged_and_subsection_dropped():
    u = units_by_ref()
    assert not u["s. 15"].in_force and not u["s. 15"].is_repealed      # not repealed: it may come into force later
    assert u["s. 16"].in_force
    assert "Not in force" not in u["s. 16"].text and "(1) No legal proceeding" in u["s. 16"].text
    assert "(2) [Not in force.]" in u["s. 16"].history_note
    assert all(x.in_force for ref, x in u.items() if ref != "s. 15")
