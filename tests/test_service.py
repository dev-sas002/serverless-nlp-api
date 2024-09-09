import pytest

from nlp_lambda.errors import ValidationError
from nlp_lambda.service import (
    Analyzer,
    NlpService,
    available_analyzers,
    register_analyzer,
)


@pytest.fixture
def service(blank_nlp):
    return NlpService(lambda: blank_nlp, max_batch_size=3, max_text_chars=200)


def test_the_model_is_not_loaded_until_it_is_needed(blank_nlp):
    loads = []
    service = NlpService(lambda: loads.append(1) or blank_nlp)

    assert service.is_loaded is False
    assert loads == []

    service.analyze(["Madrid"])

    assert service.is_loaded is True
    assert len(loads) == 1


def test_entities_carry_label_and_offsets(service):
    (result,) = service.analyze(["Ada Lovelace lived in the UK"])

    assert {"text": "Ada Lovelace", "label": "PERSON", "start_char": 0, "end_char": 12} in result
    uk = next(e for e in result if e["text"] == "the UK")
    assert uk["label"] == "GPE"
    assert "Ada Lovelace lived in the UK"[uk["start_char"] : uk["end_char"]] == "the UK"


def test_locations_are_ranked_by_frequency_not_first_appearance(service):
    """The original code named this 'sorted_by_num_appearances' but did not sort."""

    text = "Spain then the UK then Spain and Spain again, the UK"
    (locations,) = service.analyze([text], analyzer="locations")

    assert locations == ["Spain", "the UK"]


def test_locations_deduplicate(service):
    (locations,) = service.analyze(["Madrid, Madrid, Madrid"], analyzer="locations")
    assert locations == ["Madrid"]


def test_find_locations_keeps_the_original_single_text_shape(service):
    assert service.find_locations("Spain is sunnier than the UK") == ["Spain", "the UK"]


def test_batches_return_one_result_per_input(service):
    results = service.analyze(["Madrid", "Spain", "nothing here"], analyzer="locations")

    assert results == [["Madrid"], ["Spain"], []]


def test_batch_size_is_capped(service):
    with pytest.raises(ValidationError, match="max_batch_size=3"):
        service.analyze(["a", "b", "c", "d"])


def test_oversized_text_is_rejected_before_the_pipeline_runs(service):
    with pytest.raises(ValidationError, match="over the 200 limit"):
        service.analyze(["x" * 201])


def test_empty_batch_is_rejected(service):
    with pytest.raises(ValidationError, match="at least one text"):
        service.analyze([])


def test_non_string_input_is_rejected(service):
    with pytest.raises(ValidationError, match="index 1"):
        service.analyze(["fine", 42])


def test_unknown_analyzer_lists_the_known_ones(service):
    with pytest.raises(ValidationError, match="entities"):
        service.analyze(["text"], analyzer="sentiment")


def test_an_analyzer_needing_a_missing_component_says_so(service):
    """The blank pipeline has no parser, so noun_chunks cannot work."""

    with pytest.raises(ValidationError, match="parser"):
        service.analyze(["a big red ball"], analyzer="noun_chunks")


def test_components_not_needed_by_the_analyzer_are_disabled(service, monkeypatch):
    """Where the per-request time goes: the parser runs only if asked for."""

    seen = {}
    real_pipe = service.nlp.pipe

    def spy(texts, **kwargs):
        seen.update(kwargs)
        return real_pipe(texts, **kwargs)

    monkeypatch.setattr(service.nlp, "pipe", spy)
    service.nlp.add_pipe("sentencizer")

    service.analyze(["Madrid"], analyzer="locations")

    assert "sentencizer" in seen["disable"]
    assert "ner" not in seen["disable"]


def test_warm_up_reports_the_load_and_is_idempotent(service):
    first = service.warm_up()
    second = service.warm_up()

    assert first["already_warm"] is False
    assert second["already_warm"] is True
    assert first["load_seconds"] >= 0
    assert "ner" in first["components"]


def test_documents_processed_is_counted(service):
    service.analyze(["one", "two"])
    service.analyze(["three"])

    assert service.stats.documents_processed == 3


def test_a_new_analyzer_can_be_registered(service):
    register_analyzer(Analyzer("token_count", lambda doc: len(doc), (), "How many tokens"))
    try:
        assert "token_count" in available_analyzers()
        assert service.analyze(["one two three"], analyzer="token_count") == [3]
    finally:
        from nlp_lambda.service import _ANALYZERS

        _ANALYZERS.pop("token_count", None)


def test_disable_set_keeps_what_each_analyzer_actually_depends_on():
    """Locks in a bug found while capturing output for the README.

    ``noun_chunks`` with only the parser enabled returns ``[]`` rather than
    raising: it needs the POS tags from the tagger and attribute_ruler too. The
    NER component in ``en_core_web_*`` carries its own tok2vec, so ``entities``
    genuinely needs nothing but ``ner``.
    """

    from nlp_lambda.service import _ANALYZERS

    class FakeNlp:
        pipe_names = [
            "tok2vec",
            "tagger",
            "parser",
            "attribute_ruler",
            "lemmatizer",
            "ner",
        ]

    service = NlpService(FakeNlp)

    assert service._disabled_for(_ANALYZERS["noun_chunks"]) == ["lemmatizer", "ner"]
    assert service._disabled_for(_ANALYZERS["entities"]) == [
        "tok2vec",
        "tagger",
        "parser",
        "attribute_ruler",
        "lemmatizer",
    ]
