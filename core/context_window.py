import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("ContextWindow")

class ContextWindow:
    def __init__(self, max_tokens: int = 4096, safety_margin: int = 256) -> None:
        self.max_tokens = max_tokens
        self.safety_margin = safety_margin
        self.effective_limit = max_tokens - safety_margin

    def estimate_tokens(self, text: str) -> int:
        """Approximate token count using standard English heuristic (approx 4 chars/token)."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def assemble_prompt(
        self,
        system_prompt: str,
        rag_chunks: List[Dict[str, Any]],
        chat_history: List[Dict[str, str]],
        current_query: str
    ) -> str:
        """Assemble and truncate context windows to respect token budget limits."""
        sys_tokens = self.estimate_tokens(system_prompt)
        query_tokens = self.estimate_tokens(current_query)
        
        # Budget remaining for RAG and history
        remaining_budget = self.effective_limit - (sys_tokens + query_tokens)
        if remaining_budget < 0:
            logger.warning("System prompt and current query exceed effective token limit!")
            remaining_budget = 0

        # Format and budget RAG context
        formatted_rag = []
        rag_tokens = 0
        for chunk in rag_chunks:
            content = chunk.get("content", "")
            chunk_text = f"[RAG Context]: {content}\n"
            c_tokens = self.estimate_tokens(chunk_text)
            if rag_tokens + c_tokens <= remaining_budget // 2:
                formatted_rag.append(chunk_text)
                rag_tokens += c_tokens
            else:
                break

        remaining_budget -= rag_tokens

        # Format and budget Chat History (sliding window from most recent)
        formatted_history = []
        history_tokens = 0
        for msg in reversed(chat_history):
            role = msg.get("role", "user")
            text = msg.get("content", "")
            msg_text = f"{role.capitalize()}: {text}\n"
            m_tokens = self.estimate_tokens(msg_text)
            
            if history_tokens + m_tokens <= remaining_budget:
                formatted_history.insert(0, msg_text)
                history_tokens += m_tokens
            else:
                break

        # Construct final prompt string
        assembled_parts = [
            f"System: {system_prompt}\n" if system_prompt else "",
            "".join(formatted_rag),
            "".join(formatted_history),
            f"User: {current_query}\nAssistant:"
        ]
        
        final_prompt = "\n".join([p for p in assembled_parts if p])
        logger.debug(f"Assembled prompt with estimated {self.estimate_tokens(final_prompt)} tokens.")
        return final_prompt

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cw = ContextWindow(max_tokens=1024)
    sys_p = "You are an advanced AGI core engine."
    rag = [{"content": "Temporal state machines drive deterministic executions."}]
    history = [{"role": "user", "content": "What is the engine?"}, {"role": "assistant", "content": "It is a temporal engine."}]
    query = "How does state transition?"
    
    prompt = cw.assemble_prompt(sys_p, rag, history, query)
    print("Assembled Prompt Test Output:\n", prompt)
