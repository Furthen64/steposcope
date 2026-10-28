from step_explorer.step.parser import StepDocument


def test_corpus_documents_have_unique_entities_and_terminated_statements(small_corpus):
    assert small_corpus, "no STEP corpus found"
    for path in small_corpus:
        document = StepDocument.from_file(path)
        assert document.entities, path
        assert len(document.entities) == len(document.by_id), path
        assert all(entity.raw.endswith(";") for entity in document.entities), path
