import httpx

from backend.app.ingest import extract_text, split_chunks, user_facing_ingest_error


def test_split_chunks_keeps_content_order():
    text = "\n\n".join(f"Section {i} revenue margin acquisition" for i in range(60))

    chunks = split_chunks(text, target_tokens=80, overlap_tokens=5)

    assert len(chunks) > 1
    assert chunks[0].startswith("Section 0")
    assert "Section 59" in chunks[-1]


def test_extract_text_routes_csv_through_excel_mode_summary(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("Region,Revenue\nNorth,1200\n", encoding="utf-8")

    text = extract_text(csv_path, "csv")

    assert text.startswith("# Excel Mode Spreadsheet Summary")
    assert "Workbook: sales.csv" in text
    assert "Headers: Region, Revenue" in text


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
