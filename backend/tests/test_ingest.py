import httpx

from backend.app.ingest import split_chunks, user_facing_ingest_error


def test_split_chunks_keeps_content_order():
    text = "\n\n".join(f"Section {i} revenue margin acquisition" for i in range(60))

    chunks = split_chunks(text, target_tokens=80, overlap_tokens=5)

    assert len(chunks) > 1
    assert chunks[0].startswith("Section 0")
    assert "Section 59" in chunks[-1]


def test_openrouter_401_error_is_user_facing_but_preserves_details():
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/embeddings")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError(
        "Client error '401 Unauthorized' for url 'https://openrouter.ai/api/v1/embeddings'",
        request=request,
        response=response,
    )

    message = user_facing_ingest_error(exc)

    assert message.startswith("OpenRouter authentication failed.")
    assert "Settings" in message
    assert "https://openrouter.ai/api/v1/embeddings" in message
