import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

SYSTEM_PROMPT = """You are a senior business analyst.
Turn requirements-meeting notes into Jira-ready user stories.
For each story: Title; "As a <role>, I want <goal>, so that <benefit>.";
acceptance criteria in Given/When/Then; open questions if unclear.
Never invent requirements that aren't in the notes."""

async def main():
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Glob", "Read", "Write"],   # <-- the built-in tools
        permission_mode="acceptEdits",             # lets it write without asking
        max_turns=15,
    )

    async for message in query(
        prompt="Find all .txt files in meeting_notes/, read them, and write "
               "user stories to user_stories.md, grouped by feature.",
        options=options,
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print("💬", block.text)                 # Claude's thinking out loud
                elif hasattr(block, "name"):
                    print(f"🔧 Tool: {block.name} {block.input}")  # each tool call
        elif isinstance(message, ResultMessage):
            print(f"\n✅ Done: {message.subtype} | cost ${message.total_cost_usd:.4f}")

asyncio.run(main())