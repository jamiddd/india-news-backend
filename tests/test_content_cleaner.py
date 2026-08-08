"""
Pure-logic tests for app/services/content_cleaner.py — stripping publisher
boilerplate out of scraped article text. No DB/network.
"""
from app.services.content_cleaner import clean_extracted_text


class TestCleanExtractedText:
    def test_none_input_returns_none(self):
        assert clean_extracted_text(None) is None

    def test_empty_string_returns_none(self):
        assert clean_extracted_text("") is None

    def test_plain_text_passes_through(self):
        text = "This is a real article paragraph with no boilerplate."
        assert clean_extracted_text(text) == text

    def test_strips_html_tags(self):
        result = clean_extracted_text("<p>Hello <b>world</b></p>")
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Hello" in result and "world" in result

    def test_drops_leading_title_repeat_line(self):
        title = "RBI cuts repo rate by 25 bps"
        text = f"{title}\nThe actual article body starts here."
        result = clean_extracted_text(text, title=title)
        assert result == "The actual article body starts here."

    def test_title_match_is_case_and_punctuation_insensitive(self):
        title = "RBI cuts repo rate by 25 bps!"
        text = "rbi cuts repo rate by 25 bps\nReal body text follows."
        result = clean_extracted_text(text, title=title)
        assert result == "Real body text follows."

    def test_no_title_provided_keeps_first_line(self):
        text = "This looks like a title\nMore body text."
        result = clean_extracted_text(text, title=None)
        assert "This looks like a title" in result

    def test_drops_exact_match_chrome_lines(self):
        text = "Real paragraph one.\nAdvertisement\nReal paragraph two.\n- ends"
        result = clean_extracted_text(text)
        assert "Advertisement" not in result
        assert "- ends" not in result
        assert "Real paragraph one." in result
        assert "Real paragraph two." in result

    def test_truncates_at_boilerplate_marker(self):
        text = (
            "This is the real article content that matters.\n"
            "About the author: John writes about finance."
        )
        result = clean_extracted_text(text)
        assert "real article content" in result
        assert "John writes about finance" not in result

    def test_truncates_marker_with_space_before_it(self):
        # Boilerplate sometimes has no line break before it — truncate from
        # the marker's position within the line, not drop the whole line.
        text = "Breaking news happened today. Subscribe now and join our community for updates."
        result = clean_extracted_text(text)
        assert "Breaking news happened today." in result
        assert "join our community" not in result

    def test_marker_glued_with_no_whitespace_drops_whole_glued_token(self):
        # When the marker is glued directly onto the preceding text with no
        # whitespace at all (e.g. "...safety.-newsn18oc_world..." per the
        # code's own docstring example), the entire contiguous non-whitespace
        # run gets dropped as one blob, not just the matched marker onward —
        # there's no whitespace boundary to cut at inside that run, so any
        # trailing word fused onto the marker is lost along with it.
        text = "Breaking news happened.Subscribe now and join our community for updates."
        result = clean_extracted_text(text)
        # "happened" is glued onto the marker with no whitespace between
        # them, so it's swept up into the dropped blob along with it —
        # only the whitespace-separated prefix survives.
        assert result == "Breaking news"

    def test_returns_none_when_only_boilerplate_remains(self):
        text = "About the author: someone."
        result = clean_extracted_text(text)
        assert result is None

    def test_whitespace_only_lines_are_dropped(self):
        text = "Real content.\n   \n\nMore real content."
        result = clean_extracted_text(text)
        assert result == "Real content.\nMore real content."
