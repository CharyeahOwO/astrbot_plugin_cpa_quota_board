import sys
from pathlib import Path
from models import QuotaReport, QuotaProvider, QuotaAccount, QuotaItem
from renderer import QuotaCardRenderer

def main():
    renderer = QuotaCardRenderer(Path("."), high_resolution=True)
    
    # Test 1: Abnormal
    item1 = QuotaItem(id="1", label="GPT-4", percent=5, status="critical")
    item2 = QuotaItem(id="2", label="Claude", percent=15, status="warning")
    item3 = QuotaItem(id="3", label="Gemini", percent=None, status="error")
    
    acc1 = QuotaAccount(id="a1", name="acc1", display_name="user1@test.com", status="critical", items=[item1])
    acc2 = QuotaAccount(id="a2", name="acc2", display_name="user2@test.com", status="warning", items=[item2])
    acc3 = QuotaAccount(id="a3", name="acc3", display_name="user3@test.com", status="error", items=[item3])
    
    prov1 = QuotaProvider(name="OpenAI", type="openai", accounts=[acc1, acc2])
    prov2 = QuotaProvider(name="Google", type="google", accounts=[acc3])
    
    report1 = QuotaReport(
        generated_at="2026-05-13 12:00:00",
        summary={"total_accounts": 3, "critical": 1, "warning": 1, "error": 1},
        providers=[prov1, prov2]
    )
    
    out1 = renderer.render_mini_card(report1)
    print(f"Generated abnormal: {out1}")
    
    # Test 2: Healthy
    report2 = QuotaReport(
        generated_at="2026-05-13 12:00:00",
        summary={"total_accounts": 10, "ok": 10},
        providers=[]
    )
    out2 = renderer.render_mini_card(report2)
    print(f"Generated healthy: {out2}")

if __name__ == "__main__":
    main()
