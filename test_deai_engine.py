import asyncio
from lucy.core.output import _detect_ai_tells, _regex_deai, process_output_sync

def test_deai():
    print("Testing De-AI Detection...")
    text1 = "Here is a list of items:\n- Item 1\n- Item 2\n\nHope this helps! Let me know if you need anything else!"
    tells = _detect_ai_tells(text1)
    print(f"Tells found: {tells}")
    assert any(cat == "chatbot_closer" for _, _, cat in tells), "Failed to detect chatbot closer"
    
    text2 = "Furthermore, it's crucial to delve into the nuances of this dynamic landscape."
    tells2 = _detect_ai_tells(text2)
    print(f"Tells found: {tells2}")
    
    print("Testing De-AI Regex Removal...")
    clean1 = _regex_deai(text1)
    print(f"Cleaned 1:\n{clean1}")
    assert "Hope this helps" not in clean1, "Failed to remove closer"
    
    clean2 = _regex_deai(text2)
    print(f"Cleaned 2:\n{clean2}")
    assert "Furthermore" not in clean2, "Failed to remove transition"
    
    print("Testing process_output_sync...")
    text3 = "Here is the data—as requested."
    clean3 = process_output_sync(text3)
    print(f"Cleaned 3: {clean3}")
    assert "—" not in clean3, "Failed to fix em dash"
    
    print("All De-AI tests passed!")

if __name__ == "__main__":
    test_deai()
