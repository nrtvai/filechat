from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ParsedTable:
    file_id: str
    file_name: str
    rows: list[dict[str, str]]
    columns: list[str]
    delimiter: str = ","


@dataclass
class SurveyArtifactResult:
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    tool_call: dict[str, Any] = field(default_factory=dict)
    evidence_packet: dict[str, Any] = field(default_factory=dict)


THEMES: list[tuple[str, tuple[str, ...]]] = [
    ("반복 검토/교정", ("검토", "교정", "확인", "체크", "검수", "오탈자", "리뷰", "피드백")),
    ("플랫폼 업로드/제작", ("업로드", "플랫폼", "이펍", "epub", "단행본", "제작", "등록", "서지", "파일")),
    ("일정/커뮤니케이션", ("일정", "공유", "메일", "이메일", "소통", "협의", "요청", "담당자", "파트너", "커뮤니케이션")),
    ("자료/레퍼런스 탐색", ("자료", "레퍼런스", "검색", "트렌드", "모니터링", "키워드", "포트폴리오", "정보")),
    ("도구/자동화", ("자동화", "파이썬", "python", "매크로", "툴", "프로그램", "스크립트", "make", "셀레니움", "selenium")),
    ("AI 활용/전환", ("ai", "gpt", "cursor", "챗", "인공지능", "요약", "초안", "할루시네이션", "프롬프트")),
    ("의사결정/검증", ("판단", "의사결정", "검증", "신뢰", "위험", "불확실", "저작권", "리스크", "정확")),
]


def read_extracted_file_texts(session_id: str) -> list[dict[str, Any]]:
    from .database import connect

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.name, f.type, f.artifact_path, f.status
            FROM files f
            JOIN session_files sf ON sf.file_id = f.id
            WHERE sf.session_id = ? AND f.status = 'ready' AND f.artifact_path IS NOT NULL
            ORDER BY sf.attached_at
            """,
            (session_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        path = Path(row["artifact_path"])
        if not path.exists():
            continue
        out.append(
            {
                "file_id": row["id"],
                "file_name": row["name"],
                "file_type": row["type"],
                "text": path.read_text(encoding="utf-8", errors="ignore"),
            }
        )
    return out


def _sniff_delimiter(text: str, file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".tsv":
        return "\t"
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        return "\t" if "\t" in sample and sample.count("\t") > sample.count(",") else ","


def parse_table(text: str, file_id: str, file_name: str) -> ParsedTable | None:
    cleaned = text.lstrip("\ufeff").strip()
    if not cleaned or ("," not in cleaned and "\t" not in cleaned):
        return None
    delimiter = _sniff_delimiter(cleaned, file_name)
    reader = csv.DictReader(io.StringIO(cleaned), delimiter=delimiter)
    if not reader.fieldnames:
        return None
    columns = [str(column or "").strip() for column in reader.fieldnames]
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {column: str(raw.get(column) or "").strip() for column in columns}
        if any(row.values()):
            rows.append(row)
        if len(rows) >= 500:
            break
    if not rows:
        return None
    return ParsedTable(file_id=file_id, file_name=file_name, rows=rows, columns=columns, delimiter=delimiter)


def _number(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
    if cleaned in {"", ".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _looks_timestamp(column: str, values: list[str]) -> bool:
    if "timestamp" in column.lower() or "date" in column.lower() or "time" in column.lower():
        return True
    non_empty = [value for value in values if value.strip()]
    if not non_empty:
        return False
    timestampish = 0
    for value in non_empty[:20]:
        stripped = value.strip()
        if re.search(r"\d{1,4}[/-]\d{1,2}[/-]\d{1,4}", stripped) and re.search(r"\d{1,2}:\d{2}", stripped):
            timestampish += 1
    return timestampish / max(1, min(len(non_empty), 20)) >= 0.5


def _looks_identifier(column: str, values: list[str]) -> bool:
    name = column.lower()
    if any(term in name for term in ("count", "total", "amount", "score", "rating", "value", "응답", "수", "점수")):
        return False
    if name in {"id", "uuid"} or name.endswith("_id") or "identifier" in name:
        return True
    non_empty = [value for value in values if value.strip()]
    if not non_empty:
        return False
    unique_ratio = len(set(non_empty)) / len(non_empty)
    avg_len = sum(len(value) for value in non_empty) / len(non_empty)
    return unique_ratio > 0.9 and avg_len <= 24 and sum(1 for value in non_empty if _number(value) is not None) / len(non_empty) >= 0.75


def _column_kind(values: list[str]) -> str:
    non_empty = [value for value in values if value.strip()]
    if not non_empty:
        return "empty"
    lower_joined = " ".join(non_empty[:8]).lower()
    if "@" in lower_joined:
        return "email"
    if sum(1 for value in non_empty if _number(value) is not None) / len(non_empty) >= 0.75:
        return "numeric"
    avg_len = sum(len(value) for value in non_empty) / len(non_empty)
    unique_count = len(set(non_empty))
    if unique_count <= max(3, min(12, len(non_empty) // 2)) and avg_len <= 48:
        return "categorical"
    return "open_text"


def profile_table(table: ParsedTable) -> list[dict[str, Any]]:
    profile = []
    for column in table.columns:
        values = [row.get(column, "") for row in table.rows]
        kind = _column_kind(values)
        if _looks_timestamp(column, values):
            kind = "timestamp"
        elif _looks_identifier(column, values):
            kind = "identifier"
        profile.append(
            {
                "name": column,
                "kind": kind,
                "non_empty": sum(1 for value in values if value.strip()),
                "unique": len(set(value for value in values if value.strip())),
                "avg_length": round(sum(len(value) for value in values if value.strip()) / max(1, sum(1 for value in values if value.strip())), 1),
            }
        )
    return profile


def _source_for_file(file_id: str, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    for source in sources:
        if source.get("file_id") == file_id:
            return source
    return sources[0] if sources else None


def _subject_from_file_name(file_name: str) -> str:
    subject = unicodedata.normalize("NFC", file_name)
    subject = re.sub(r"\.[a-z0-9]+$", "", subject, flags=re.IGNORECASE)
    subject = subject.replace("(Responses)", " ")
    subject = re.sub(r"-\s*Form Responses\s*\d*", " ", subject, flags=re.IGNORECASE)
    subject = " ".join(subject.split()).strip(" -_")
    return subject or "설문"


def _slug_filename(subject: str, suffix: str) -> str:
    cleaned = re.sub(r"[^\w가-힣]+", "-", subject, flags=re.UNICODE).strip("-").lower()
    cleaned = re.sub(r"-+", "-", cleaned)
    return f"{cleaned or 'survey'}-{suffix}.md"


def _categorical_chart(table: ParsedTable, profile: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [item for item in profile if item["kind"] == "categorical"]
    if not candidates:
        return None
    column = candidates[0]["name"]
    counts: dict[str, int] = {}
    for row in table.rows:
        label = row.get(column, "").strip()
        if label:
            counts[label] = counts.get(label, 0) + 1
    values = [
        {"label": label, "value": count, "source_id": source["source_id"], "source_chunk_id": source["chunk_id"]}
        for label, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    if not values:
        return None
    return {
        "kind": "chart",
        "title": f"{_subject_from_file_name(table.file_name)}: 응답 분포",
        "caption": f"Deterministic count by '{column}' from {len(table.rows)} survey row(s).",
        "display_mode": "primary",
        "source_ids": [source["source_id"]],
        "source_chunk_ids": [source["chunk_id"]],
        "chart_type": "bar",
        "x_label": column[:48],
        "y_label": "Responses",
        "values": values,
    }


def _numeric_pair_chart(table: ParsedTable, profile: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any] | None:
    numeric_columns = [item["name"] for item in profile if item["kind"] == "numeric" and not _is_bad_measure_name(item["name"])]
    label_columns = [item["name"] for item in profile if item["kind"] in {"categorical", "open_text"}]
    if not numeric_columns or not label_columns:
        return None
    label_column = label_columns[0]
    value_column = numeric_columns[0]
    totals: dict[str, float] = {}
    for row in table.rows:
        label = row.get(label_column, "").strip()
        number = _number(row.get(value_column, ""))
        if label and number is not None:
            totals[label] = totals.get(label, 0.0) + number
    values = [
        {"label": label, "value": value, "source_id": source["source_id"], "source_chunk_id": source["chunk_id"]}
        for label, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    if not values:
        return None
    return {
        "kind": "chart",
        "title": f"{_subject_from_file_name(table.file_name)}: 수치 집계",
        "caption": f"Deterministic values by '{label_column}' using '{value_column}' from {len(table.rows)} survey row(s).",
        "display_mode": "primary",
        "source_ids": [source["source_id"]],
        "source_chunk_ids": [source["chunk_id"]],
        "chart_type": "bar",
        "x_label": label_column[:48],
        "y_label": value_column[:48],
        "values": values,
    }


def _is_bad_measure_name(name: str) -> bool:
    lowered = name.lower()
    return any(term in lowered for term in ("timestamp", "email", "address", "id", "identifier", "uuid"))


def _theme_for_text(text: str) -> str:
    normalized = text.lower()
    scores = [(label, sum(normalized.count(keyword.lower()) for keyword in keywords)) for label, keywords in THEMES]
    label, score = max(scores, key=lambda item: item[1])
    return label if score > 0 else "기타"


def _theme_chart(table: ParsedTable, profile: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any] | None:
    open_columns = [item["name"] for item in profile if item["kind"] == "open_text"]
    if not open_columns:
        return None
    counts: dict[str, int] = {}
    for row in table.rows:
        combined = " ".join(row.get(column, "") for column in open_columns).strip()
        if not combined:
            continue
        theme = _theme_for_text(combined)
        counts[theme] = counts.get(theme, 0) + 1
    if not counts:
        return None
    values = [
        {"label": label, "value": count, "source_id": source["source_id"], "source_chunk_id": source["chunk_id"]}
        for label, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    return {
        "kind": "chart",
        "title": f"{_subject_from_file_name(table.file_name)}: 응답 주제 분포",
        "caption": f"{len(table.rows)}건의 주관식 응답에서 반복된 주제별 빈도입니다.",
        "display_mode": "primary",
        "source_ids": [source["source_id"]],
        "source_chunk_ids": [source["chunk_id"]],
        "chart_type": "bar",
        "x_label": "주제",
        "y_label": "응답 수",
        "values": values,
    }


def _table_artifact(table: ParsedTable, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "table",
        "title": f"{_subject_from_file_name(table.file_name)}: 원자료 미리보기",
        "caption": f"{len(table.rows)}건 중 첫 {min(len(table.rows), 12)}건의 원자료입니다.",
        "display_mode": "supporting",
        "source_ids": [source["source_id"]],
        "source_chunk_ids": [source["chunk_id"]],
        "columns": table.columns[:8],
        "rows": [{column: row.get(column, "") for column in table.columns[:8]} for row in table.rows[:12]],
    }


def _analysis_draft(table: ParsedTable, profile: list[dict[str, Any]], source: dict[str, Any], chart: dict[str, Any] | None) -> dict[str, Any]:
    chart_lines: list[str] = []
    if chart:
        for item in chart.get("values", [])[:8]:
            if isinstance(item, dict):
                chart_lines.append(f"- {item.get('label')}: {item.get('value')}")
    open_text_columns = [item for item in profile if item["kind"] == "open_text"]
    subject = _subject_from_file_name(table.file_name)
    filename = _slug_filename(subject, "분석-초안")
    insight_lines = []
    if chart and "응답 주제 분포" in str(chart.get("title") or ""):
        top_values = [item for item in chart.get("values", []) if isinstance(item, dict)]
        for item in top_values[:5]:
            insight_lines.append(f"- {item.get('label')}: {item.get('value')}건의 응답에서 반복적으로 나타났습니다.")
    elif chart_lines:
        insight_lines.append("- 아래 분포는 첨부 설문 원자료에서 직접 집계한 값입니다.")
    else:
        insight_lines.append("- 정량 집계보다 응답 원문 검토가 필요한 설문 구조입니다.")
    content = "\n".join(
        [
            f"# {subject}: 분석 초안",
            "",
            "## 데이터 개요",
            f"- 파일: {table.file_name}",
            f"- 행 수: {len(table.rows)}",
            f"- 열 수: {len(table.columns)}",
            f"- 주관식 문항 수: {len(open_text_columns)}",
            "",
            "## 핵심 인사이트",
            *insight_lines,
            "",
            "## 차트 요약",
            *(chart_lines or ["- 수치형/범주형 조합을 찾지 못해 원자료 표를 우선 확인해야 합니다."]),
            "",
            "## 해석",
            "- 반복 빈도가 높은 주제는 개인의 숙련도 문제보다 업무 흐름과 검수 체계의 병목일 가능성이 큽니다.",
            "- 단순 자동화 후보와 판단/검증이 필요한 업무를 분리하면 후속 실험 우선순위를 잡기 쉽습니다.",
            "",
            "## 권장 후속 액션",
            "- 상위 주제별 대표 응답을 검토해 실제 병목 업무를 정의합니다.",
            "- 반복 빈도가 높은 주제부터 자동화/템플릿화 후보로 분리합니다.",
            "- 의사결정용 공유 자료라면 각 주제별 대표 인용문과 담당 조직 맥락을 보강합니다.",
            "",
            "## 방법",
            "- 첨부 파일에서 확인 가능한 값만 사용했습니다.",
            "- 이메일, 타임스탬프, 식별자는 분석 지표에서 제외했습니다.",
            "- 차트와 표는 출처 청크에 연결됩니다.",
        ]
    )
    return {
        "kind": "file_draft",
        "title": f"{subject}: 분석 초안",
        "caption": "첨부 설문 데이터를 기반으로 만든 Markdown 초안입니다.",
        "display_mode": "primary",
        "source_ids": [source["source_id"]],
        "source_chunk_ids": [source["chunk_id"]],
        "filename": filename,
        "format": "markdown",
        "content": content,
    }


def _representative_examples(table: ParsedTable, profile: list[dict[str, Any]], limit: int = 6) -> list[dict[str, str]]:
    open_columns = [item["name"] for item in profile if item["kind"] == "open_text"]
    examples: list[dict[str, str]] = []
    for row in table.rows:
        combined = " ".join(row.get(column, "") for column in open_columns).strip()
        if not combined:
            continue
        theme = _theme_for_text(combined)
        examples.append({"theme": theme, "excerpt": re.sub(r"\s+", " ", combined)[:260]})
        if len(examples) >= limit:
            break
    return examples


def _evidence_packet(
    *,
    table: ParsedTable,
    profile: list[dict[str, Any]],
    source: dict[str, Any],
    chart: dict[str, Any] | None,
    question: str,
) -> dict[str, Any]:
    subject = _subject_from_file_name(table.file_name)
    theme_counts = chart.get("values", []) if chart and isinstance(chart.get("values"), list) else []
    usable_columns = [item for item in profile if item["kind"] not in {"email", "timestamp", "identifier"}]
    return {
        "recommended_title": f"{subject}: 분석 초안",
        "recommended_filename": _slug_filename(subject, "분석-초안"),
        "draft_caption": "근거 패킷과 설문 원자료를 바탕으로 작성한 Markdown 분석 초안입니다.",
        "user_request": question,
        "dataset": {
            "file_name": table.file_name,
            "subject": subject,
            "row_count": len(table.rows),
            "column_count": len(table.columns),
            "open_text_question_count": sum(1 for item in profile if item["kind"] == "open_text"),
            "source_id": source["source_id"],
            "source_chunk_id": source["chunk_id"],
        },
        "theme_counts": theme_counts,
        "representative_examples": _representative_examples(table, profile),
        "usable_columns": [
            {"name": item["name"], "kind": item["kind"], "non_empty": item["non_empty"], "unique": item["unique"]}
            for item in usable_columns[:10]
        ],
        "caveats": [
            "타임스탬프, 이메일, 식별자는 분석 지표에서 제외했습니다.",
            "주관식 주제 분류는 응답 텍스트의 반복 키워드와 의미 신호를 기반으로 합니다.",
        ],
        "suggested_sections": ["핵심 요약", "주요 발견", "업무 병목 해석", "권장 액션", "검토할 질문"],
    }


def build_survey_artifacts(question: str, file_texts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> SurveyArtifactResult:
    normalized = question.lower()
    asks_for_chart = any(word in normalized for word in ("chart", "graph", "plot", "survey", "설문", "차트", "그래프"))
    asks_for_material = any(word in normalized for word in ("draft", "report", "document", "analysis", "insight")) or any(
        word in question for word in ("분석", "자료", "보고서", "문서", "초안", "제작", "작성")
    )
    if not asks_for_chart and not asks_for_material:
        return SurveyArtifactResult()

    parsed_tables: list[tuple[ParsedTable, list[dict[str, Any]]]] = []
    for item in file_texts:
        table = parse_table(str(item.get("text") or ""), str(item["file_id"]), str(item["file_name"]))
        if table:
            parsed_tables.append((table, profile_table(table)))

    for table, profile in parsed_tables:
        source = _source_for_file(table.file_id, sources)
        if not source:
            continue
        theme_chart = _theme_chart(table, profile, source)
        chart = _numeric_pair_chart(table, profile, source) or _categorical_chart(table, profile, source) or theme_chart
        if asks_for_material and theme_chart:
            chart = theme_chart
        artifacts: list[dict[str, Any]] = []
        if chart and (asks_for_chart or asks_for_material):
            artifacts.append(chart)
        if asks_for_material:
            artifacts.append(_table_artifact(table, source))
            artifacts.append(_analysis_draft(table, profile, source, chart))
        if artifacts:
            evidence_packet = _evidence_packet(table=table, profile=profile, source=source, chart=chart, question=question)
            return SurveyArtifactResult(
                artifacts=artifacts,
                summary=f"Parsed {len(table.rows)} row(s) from {table.file_name} and built {len(artifacts)} deterministic artifact(s).",
                tool_call={
                    "tool": "survey_profiler",
                    "file_id": table.file_id,
                    "file_name": table.file_name,
                    "row_count": len(table.rows),
                    "column_profile": profile,
                    "artifact_count": len(artifacts),
                    "evidence_packet": evidence_packet,
                },
                evidence_packet=evidence_packet,
            )
    return SurveyArtifactResult(
        summary="No chartable survey table was detected in ready source files.",
        tool_call={"tool": "survey_profiler", "row_count": 0, "artifact_kind": None},
    )
