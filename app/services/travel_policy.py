"""Source-faithful replies for programme-specific Travel policy questions."""
from __future__ import annotations

import re
import unicodedata

MAX_POLICY_QUOTE_CHARS = 4000


def _unavailable(locale: str) -> str:
    return {
        "en": (
            "I couldn't verify the matching tour terms from a source with complete "
            "programme and version details. Please share the tour code or name."
        ),
        "zh-Hans": "我无法从版本信息完整的行程来源核实对应条款。请提供行程代码或名称。",
    }.get(locale, "Mình chưa thể xác minh điều khoản từ nguồn có đủ thông tin tour và phiên bản. "
                     "Bạn cho mình biết mã hoặc tên tour nhé.")


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = value.replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value)).strip()


def _question_identifies_source(question: str, source: str) -> bool:
    question_normalized = _normalize(question)
    source_stem = source.rsplit(".", 1)[0]
    source_normalized = _normalize(source_stem)
    if source_normalized and source_normalized in question_normalized:
        return True
    code = re.match(r"^((?:hvnt|hvtb)\s+\d{3,4}\s+\d{2})\b", source_normalized)
    return bool(code and code.group(1) in question_normalized)


def _confirm_source_reply(source: str, locale: str) -> str:
    code_ticks = "`" * (max((len(match) for match in re.findall(r"`+", source)), default=0) + 1)
    safe_source = f"{code_ticks}{source}{code_ticks}"
    return {
        "en": (
            f"Tour terms vary by programme. I found terms in {safe_source}. "
            "Please confirm this is your tour or share its exact code or full name before I quote them."
        ),
        "zh-Hans": (
            f"不同 tour 的条款可能不同。我在 {safe_source} 中找到条款。"
            "请先确认这是你预订的行程，或提供准确代码或完整名称，再核对原文。"
        ),
    }.get(locale, f"Điều khoản tùy từng tour. Mình tìm thấy điều khoản trong {safe_source}. "
                     "Bạn xác nhận đây là tour của mình hoặc gửi đúng mã/tên đầy đủ trước khi mình trích nhé.")


def source_faithful_policy_reply(
    chunks: list[dict], locale: str = "vi", *, question: str = ""
) -> str:
    """Quote one retrieved programme's extracted policy text and name its file.

    The retriever filters policy chunks for policy questions. Text pieces from
    only the top source and active version are joined in source order.
    """
    policy_chunks = [
        chunk for chunk in chunks
        if (chunk.get("metadata") or {}).get("section") == "Policy"
    ]
    if not policy_chunks:
        return _unavailable(locale)

    first_metadata = policy_chunks[0].get("metadata") or {}
    source = first_metadata.get("filename") or first_metadata.get("source")
    document_id = first_metadata.get("document_id")
    version_id = first_metadata.get("document_version_id")
    # Ingested chunks always carry these identities. Without them we cannot
    # prove that additional pieces belong to this exact PDF version.
    if not source or not document_id or not version_id:
        return _unavailable(locale)
    # Include other retrieved pieces only when they belong to this same PDF.
    # The deterministic quote never combines terms from sibling programmes.
    same_source = [
        chunk for chunk in policy_chunks
        if (
            (chunk.get("metadata") or {}).get("document_id") == document_id
            and (chunk.get("metadata") or {}).get("document_version_id") == version_id
        )
    ]
    if len(same_source) != len(policy_chunks):
        # Retrieval should not return multiple tour documents for policy asks;
        # if it does, fail closed rather than deciding from mixed evidence.
        return _unavailable(locale)
    same_source.sort(
        key=lambda chunk: (chunk.get("metadata") or {}).get("chunk_index", 0)
    )
    metadata = first_metadata
    source = metadata.get("filename") or metadata.get("source")
    if not question or not _question_identifies_source(question, source):
        return _confirm_source_reply(source, locale)
    code_ticks = "`" * (max((len(match) for match in re.findall(r"`+", source)), default=0) + 1)
    safe_source = f"{code_ticks}{source}{code_ticks}"

    source_parts: list[str] = []
    for chunk in same_source:
        text = str(chunk.get("text") or "")
        _, separator, source_text = text.partition("\n\n")
        source_parts.append((source_text if separator else text).strip())
    excerpt = ""
    for part in source_parts:
        if not excerpt:
            excerpt = part
            continue
        overlap = min(180, len(excerpt), len(part))
        while overlap >= 16 and not excerpt.endswith(part[:overlap]):
            overlap -= 1
        excerpt += part[overlap:]
    if len(excerpt) > MAX_POLICY_QUOTE_CHARS:
        return {
            "en": f"The policy section in {safe_source} is too long to quote safely in one reply. Please ask about one term at a time.",
            "zh-Hans": f"{safe_source} 的条款内容过长，无法在一条回复中完整引用。请一次询问一项条款。",
        }.get(
            locale,
            f"Phần điều khoản trong {safe_source} quá dài để trích an toàn "
            "trong một câu trả lời. Bạn hãy hỏi từng điều khoản nhé.",
        )
    quoted = "\n".join(f"> {line}" if line else ">" for line in excerpt.splitlines())
    prefix = {
        "en": f"According to {safe_source}, the programme states verbatim:\n\n",
        "zh-Hans": f"根据 {safe_source}，行程原文如下：\n\n",
    }.get(locale, f"Theo {safe_source}, điều khoản được ghi nguyên văn:\n\n")
    return prefix + quoted
