# Catalogue dữ liệu địa điểm, dịch vụ, ảnh và đánh giá

Thư mục này chứa **catalogue có cấu trúc**: địa điểm, dịch vụ, ảnh và đánh giá,
cùng bộ câu hỏi vàng dùng để đo chất lượng gợi ý.

Nó **không phải** kho RAG. Quan hệ giữa các nguồn dữ liệu trong dự án:

| Thư mục | Bản chất | Đi vào đâu | Ai giữ |
|---|---|---|---|
| `data/` | PDF tài liệu du lịch thật | Parser → embedding → vector store | Người 2 |
| `data/demo/` | Corpus hư cấu để kiểm thử đường ống RAG | Cùng đường ống trên | Người 2 |
| `demo_data/` | Bộ dữ liệu thử nghiệm proactive C01-F1.0 | Nghiên cứu proactive | Người 2 |
| **`datasets/`** | **Catalogue có cấu trúc: địa điểm, dịch vụ, ảnh, đánh giá** | **Tra cứu theo ID, không embedding** | **Người 1** |

## Vì sao phải tách khỏi kho RAG

Ảnh và đánh giá **không được** đưa vào vector store, vì ba lý do độc lập:

1. **Pháp lý.** Đánh giá và ảnh lấy từ nguồn ngoài (Google Places, TripAdvisor…)
   có bản quyền. Điều khoản của các nhà cung cấp này cho phép *hiển thị kèm ghi
   nguồn*, nhưng cấm sao chép vào kho dữ liệu riêng, và càng cấm đưa vào dữ liệu
   huấn luyện mô hình. Nếu chúng nằm trong chunk RAG thì không thể gỡ ra được nữa.
2. **Ranh giới an toàn của dự án.** Kế hoạch triển khai hiện hành quy định mọi
   tri thức mới bắt buộc phải qua hàng đợi kiểm duyệt trước khi vào vector store.
   Ảnh và đánh giá kéo về tự động không thỏa mãn điều kiện đó.
3. **Kỹ thuật.** Một URL ảnh hay một điểm số 4.3/5 không có ngữ nghĩa để nhúng
   vector. Nhúng chúng chỉ làm loãng không gian embedding và tốn chi phí.

Cách đúng: RAG trả lời **địa điểm nào phù hợp**, catalogue này cung cấp **ảnh,
điểm số và đánh giá của địa điểm đó** thông qua tra cứu theo ID.

## Nguyên tắc cốt lõi: một schema, hai nguồn

Yêu cầu của dự án là *"chatbot học tốt trên dữ liệu demo, khi thay bằng dữ liệu
thật vẫn làm tốt"*. Điều đó chỉ đạt được nếu dữ liệu demo và dữ liệu thật **có
cùng hình dạng**, không phải cùng nội dung.

```
                     ┌─────────────────────────────┐
                     │  catalogue.schema.json      │
                     │  (một hợp đồng duy nhất)    │
                     └──────────────┬──────────────┘
                                    │
              ┌─────────────────────┴─────────────────────┐
              │                                           │
   ┌──────────▼───────────┐                   ┌───────────▼──────────┐
   │ Nguồn SYNTHETIC      │                   │ Nguồn LIVE           │
   │ generate_demo_       │                   │ (Google Places,      │
   │ catalogue.py         │                   │  Mapbox, đối tác…)   │
   │                      │                   │                      │
   │ license.can_store    │                   │ license.can_store    │
   │   = true             │                   │   = false            │
   │ provenance.source    │                   │ provenance.source    │
   │   = "synthetic"      │                   │   = "google_places"  │
   └──────────┬───────────┘                   └───────────┬──────────┘
              │                                           │
              └─────────────────────┬─────────────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │  validate_catalogue.py      │
                     │  cùng một bộ kiểm tra       │
                     └──────────────┬──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │  Ứng dụng: tra cứu theo ID  │
                     │  Không biết nguồn nào       │
                     └─────────────────────────────┘
```

Ứng dụng đọc `license.can_store` chứ không đọc tên nhà cung cấp. Nhờ vậy việc
chuyển từ demo sang thật là đổi cấu hình, không phải viết lại mã.

## Trường `license` quyết định điều gì được phép

Mỗi ảnh và mỗi đánh giá đều bắt buộc mang khối `license`:

| Trường | Ý nghĩa khi `true` | Hệ quả khi `false` |
|---|---|---|
| `can_store` | Được lưu vào cơ sở dữ liệu của mình | Chỉ được giữ trong bộ nhớ đệm có hạn dùng, hết hạn phải bỏ |
| `can_redistribute` | Được hiển thị lại ở nơi khác, được xuất ra tệp | Chỉ hiển thị tại chỗ, không xuất, không chia sẻ |
| `can_train` | Được đưa vào dữ liệu huấn luyện mô hình | **Tuyệt đối không** được vào tập huấn luyện |
| `display_attribution` | Chuỗi bắt buộc hiển thị kèm | — |

Bộ dữ liệu demo đặt cả ba cờ là `true` vì nội dung do chính dự án sinh ra. Dữ
liệu thật từ nhà cung cấp bên ngoài gần như luôn có `can_train = false`, và
thường có `can_store = false`. Trình kiểm tra sẽ chặn nếu một bản ghi
`can_train = false` lọt vào tập xuất huấn luyện.

## Bộ câu hỏi vàng không được nhắc tên địa điểm

`golden/queries.json` mô tả câu hỏi theo **thuộc tính**, không theo tên riêng:

```
KHÔNG viết:  "Hỏi về Bến Gió Xanh thì phải trả về Bến Gió Xanh"
PHẢI viết:   "Trời mưa + có trẻ nhỏ + dưới 50.000đ
              → mọi kết quả phải có indoor=true và giá ≤ 50.000đ"
```

Chỉ cách viết thứ hai mới còn đúng sau khi thay toàn bộ dữ liệu demo bằng dữ
liệu thật. Đây là điều kiện bắt buộc để phép thử hoán đổi có ý nghĩa.

## Cảnh báo về huấn luyện mô hình

Nếu đem nội dung demo trong thư mục này đi tinh chỉnh trọng số mô hình, mô hình
sẽ **thuộc lòng những địa điểm không có thật** và đọc lại cho khách thật. Chỉ
được huấn luyện trên **hình dạng phán đoán** — cách cân bằng ràng buộc, cách sắp
thứ tự, cách xin thêm thông tin khi thiếu dữ liệu — chứ không bao giờ trên các
sự kiện cụ thể trong bộ demo.

## Lệnh

```powershell
# Sinh lại toàn bộ catalogue demo (kết quả tất định, cùng seed cho cùng kết quả)
python scripts/generate_demo_catalogue.py

# Kiểm tra một catalogue bất kỳ, demo hay thật, theo cùng một hợp đồng
python scripts/validate_catalogue.py datasets/demo/catalogue.json
```

Cả hai script chỉ dùng thư viện chuẩn của Python. Đây là lựa chọn có chủ đích:
`requirements.txt` là tệp giao thoa giữa hai người, nên tránh thêm phụ thuộc mới
khi chưa cần thiết.

## Ứng dụng dùng catalogue thế nào (D1)

Khi khách hỏi Travel kiểu *đi đâu / ăn gì / chỗ trong nhà khi mưa* ở một điểm
đến có trong catalogue, `app/services/place_cards.py` chọn tối đa 3 địa điểm và
gắn vào sự kiện `done` của stream (`places`). PWA vẽ thành thẻ dưới câu trả lời.

- Chỉ lấy địa điểm của **điểm đến được nhắc tên** trong câu hỏi. Nơi không có
  trong catalogue (Hà Nội, Đà Nẵng…) thì không có thẻ nào.
- Không bao giờ gửi giá. Thẻ `license.type = synthetic-demo` luôn có nhãn
  "Dữ liệu minh họa".
- Ảnh đi qua `GET /api/v1/media/catalogue/{tên}.svg`, chỉ phục vụ tệp được khai
  trong catalogue và nằm trong thư mục `media/` cạnh tệp catalogue.
- Tệp không đọc được, sai JSON, hoặc **bất kỳ** mục nào thiếu cờ
  `can_store` / `can_redistribute` / `can_train` / `type` thì toàn bộ thẻ tắt
  (fail closed). Ứng dụng vẫn trả lời bình thường, chỉ không có thẻ.

## Thay dữ liệu demo bằng dữ liệu thật (D4)

```powershell
# 1. Tệp mới phải qua đúng hợp đồng như bản demo
python scripts/validate_catalogue.py path/to/real/catalogue.json

# 2. So với bản demo: báo tên từng câu hỏi vàng bị thụt lùi
python scripts/validate_catalogue.py path/to/real/catalogue.json --compare datasets/demo/catalogue.json
```

3. Đặt ảnh vào thư mục `media/` **cạnh** tệp catalogue mới (`uri` trong tệp
   phải trỏ vào trong thư mục đó; trỏ ra ngoài thì ảnh bị bỏ qua).
4. Trong `.env`: `CATALOGUE_PATH=path/to/real/catalogue.json`, rồi khởi động lại.
   Muốn tắt hẳn thẻ: `CATALOGUE_ENABLED=false`.
5. Hỏi thử một câu có tên điểm đến mới, ví dụ "Ở <tên> có gì ăn ngon?", xem
   thẻ hiện ra. Dữ liệu thật **không** có nhãn "Dữ liệu minh họa" — nhãn đọc từ
   `license.type`, không đoán theo tên nguồn.

Bước 1–2 là chốt chặn: không qua thì không đổi `CATALOGUE_PATH`.

## Trạng thái

Dữ liệu trong `datasets/demo/` là **hư cấu, không có thẩm quyền**
(`synthetic`, `non-authoritative`). Không được dùng làm bằng chứng cho bất kỳ
tuyên bố nào về mức độ sẵn sàng vận hành.
