#!/usr/bin/env python
"""Test the smart thread message classification logic."""

import sys
sys.path.insert(0, "src")

from lucy.slack.thread_manager import ThreadManager


def test_classification():
    """Test message classification patterns."""
    tm = ThreadManager()
    
    test_cases = [
        # (text, sender, initiator, participants, expected_for_lucy)
        
        # Should be detected as FOR Lucy
        ("what about tomorrow?", "U1", "U1", ["U1"], True),
        ("can you check that again?", "U1", "U1", ["U1"], True),
        ("and what time is the meeting?", "U1", "U1", ["U1"], True),
        ("how do I schedule that?", "U1", "U1", ["U1"], True),
        ("wait, what about the other one?", "U1", "U1", ["U1"], True),
        ("also, can you add another?", "U1", "U1", ["U1"], True),
        ("ok lucy, what else?", "U1", "U1", ["U1"], True),
        
        # Short questions - likely follow-ups
        ("what time?", "U1", "U1", ["U1"], True),
        ("where?", "U1", "U1", ["U1"], True),
        ("when is it?", "U1", "U1", ["U1"], True),
        
        # Should NOT be for Lucy (talking to others)
        ("<@U2> what do you think?", "U1", "U1", ["U1"], False),
        ("you guys ready for the meeting?", "U1", "U1", ["U1", "U2"], False),
        ("what do you all think about this?", "U1", "U1", ["U1", "U2"], False),
        ("anyone want to join?", "U1", "U1", ["U1", "U2"], False),
        ("let's discuss this tomorrow", "U1", "U1", ["U1"], False),
        ("sounds good to me", "U1", "U1", ["U1", "U2"], False),
        ("agree?", "U1", "U1", ["U1", "U2"], False),
        
        # Exit phrases
        ("thanks!", "U1", "U1", ["U1"], False),
        ("got it, thanks", "U1", "U1", ["U1"], False),
        ("that's all", "U1", "U1", ["U1"], False),
        
        # Third party responding - shift detected
        ("I'm good with that", "U2", "U1", ["U1", "U2"], False),
        ("works for me", "U2", "U1", ["U1", "U2"], False),
    ]
    
    passed = 0
    failed = 0
    
    for text, sender, initiator, participants, expected in test_cases:
        result = tm._classify_message_intent(text, sender, initiator, participants)
        actual = result["for_lucy"]
        
        status = "✓" if actual == expected else "✗"
        if actual == expected:
            passed += 1
        else:
            failed += 1
        
        print(f"{status} '{text[:40]}' - expected: {expected}, got: {actual} ({result['type']})")
    
    print(f"\n{passed}/{len(test_cases)} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = test_classification()
    sys.exit(0 if success else 1)
