from cse_hq_bot.ai.base import AIMessage, RetrievedContextRecord


def render_provider_prompt(
    system_instruction: str,
    messages: list[AIMessage],
    context_records: list[RetrievedContextRecord],
) -> str:
    """Render one provider-neutral prompt package for every AI backend."""
    conversation = "\n".join(
        f"{message.role.upper()}: {message.content}" for message in messages
    )
    context = "\n".join(
        (
            f"[{record.source_id}] type={record.source_type} title={record.title} "
            f"timestamp={record.timestamp or 'unknown'} reason={record.retrieval_reason}\n"
            f"{record.content}"
        )
        for record in context_records
    )
    return (
        f"<SYSTEM_INSTRUCTIONS>\n{system_instruction}\n</SYSTEM_INSTRUCTIONS>\n\n"
        f"<PROJECT_CONTEXT>\n{context or 'No matching project records were retrieved.'}\n"
        "</PROJECT_CONTEXT>\n\n"
        f"<CONVERSATION>\n{conversation}\n</CONVERSATION>"
    )
