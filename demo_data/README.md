# VietSage proactive demo data

Bo du lieu nay la du lieu gia lap. Moi ten khach san, phong, khach luu tru,
gia dich vu va dia diem co nhan `DEMO` deu khong dai dien cho mot giao dich
thuc te.

## Muc tieu

- Kiem thu RAG voi thong tin khach san, dich vu va diem den co dap an ro rang.
- Kiem thu proactive recommendation theo moc thoi gian bang simulated clock.
- Kiem thu consent o ba trang thai: granted, denied va withdrawn.
- Kiem thu fallback khi khong co GPS, khong co push permission hoac thieu data.
- Kiem thu idempotency va frequency cap truoc khi ket noi PMS that.

## Tep chinh

- `vietsage_demo_source.json`: source of truth co cau truc.
- `expected_proactive_cases.json`: test oracle cho rule engine.
- `../output/pdf/VSDEMO 0001-26.pdf`: nguon RAG sinh tu JSON.
- `manifest.json`: mapping de dung voi ingestion script hien tai.

## Tao lai PDF va kiem tra

```powershell
python scripts/generate_demo_source_pdf.py
python scripts/validate_demo_data.py
python scripts/ingest_travel_data.py --data-root output/pdf --manifest demo_data/manifest.json --dry-run
```

Khong chay `--prune` voi manifest demo tren database dang chua data that.
Neu can ingest, nen dung mot `DATABASE_URL` rieng cho demo.
