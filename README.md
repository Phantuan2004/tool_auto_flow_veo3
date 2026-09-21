# GFlow Veo Batch Studio

Ứng dụng PySide6 chạy lần lượt các phân cảnh qua [`gflow-cli`](https://github.com/ffroliva/gflow-cli). Mỗi batch tạo (hoặc dùng) **một Google Flow Project chung**. Với từng cảnh, app tạo **1 ảnh Nano Banana 2**, nhận `media UUID` của ảnh từ Flow và truyền UUID đó thẳng vào I2V để tạo **1 video Omni Flash, 8 giây** trong đúng project đó — không tải ảnh về rồi upload lại.

> `gflow-cli` là công cụ không chính thức, đang ở giai đoạn alpha. Nó dùng phiên Google Flow của bạn; các lượt tạo có thể tiêu tốn credit và chịu điều khoản của Google Flow.

## Yêu cầu

- Python 3.10+.
- PySide6 để chạy giao diện desktop.
- Một tài khoản đã có quyền dùng Google Flow/Veo và credit phù hợp.
- `gflow-cli` cài trong **cùng môi trường Python** dùng để chạy ứng dụng.

## Cài đặt

Mở PowerShell trong thư mục dự án rồi chạy:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
gflow --help
```

Ở lần chạy batch đầu tiên, ứng dụng tự kiểm tra profile. Nếu chưa có, nó chạy `gflow auth login` và mở cửa sổ trình duyệt; hãy đăng nhập tài khoản Google có quyền Google Flow/Veo, hoàn tất xác nhận trong trình duyệt, rồi chờ app tự tiếp tục. Bạn cũng có thể chủ động đăng nhập từ PowerShell:

```powershell
gflow auth login
gflow auth status
```

Nút **Kiểm tra gflow** chỉ kiểm tra phiên bản CLI, không thực hiện đăng nhập.

## Chạy ứng dụng

```powershell
.\.venv\Scripts\Activate.ps1
python .\main.py
```

Giao diện mới chia thành hai cột:

- Bên trái là **Media Gallery**, xem ảnh đã tạo, đường dẫn video và nút mở file theo scene.
- Bên phải là **Batch Configuration**, chọn kịch bản/thư mục output, Project ID, tỷ lệ, danh sách scene và chỉnh prompt ảnh/video.
- Thanh công cụ có nút kiểm tra `gflow`, tạo ảnh, tạo video và dừng tiến trình.
- Dry-run được bật mặc định để kiểm tra luồng mà không tiêu credit.

Không cần chạy trực tiếp `gflow_veo_batcher.py`; file đó giữ phần backend tương thích với phiên bản cũ.

## Định dạng tệp cảnh

Hỗ trợ JSON, JSONL/NDJSON, CSV/TSV, hoặc TXT (một prompt trên mỗi dòng). Các trường dùng được:

| Trường | Bắt buộc | Mô tả |
|---|---:|---|
| `id` | Không | Tên cảnh/thư mục output. Mặc định `scene-001`... |
| `prompt` | Có* | Prompt dùng cho cả bước ảnh và video. |
| `image_prompt` | Có* | Prompt riêng khi tạo ảnh Nano Banana 2. |
| `video_prompt` | Không | Prompt chuyển động riêng cho video; mặc định dùng `prompt` (hoặc `image_prompt`). |
| `references` | Không | Danh sách đường dẫn ảnh tham chiếu cho scene: nhân vật, đồ vật, bối cảnh... Tất cả ảnh được gửi cùng lúc khi tạo ảnh. |
| `aspect` | Không | `16:9` hoặc `9:16`. |
| `project` | Không | Google Flow Project ID cho cảnh đó. |

*Một cảnh cần có ít nhất `prompt`, `image_prompt` hoặc `video_prompt`. Trong JSON, `defaults` cấp các giá trị chung; một cảnh có giá trị riêng sẽ ghi đè.

CSV mẫu có header: `id,prompt,image_prompt,video_prompt,references,aspect,project`. Với CSV, nhiều ảnh tham chiếu có thể ngăn cách bằng dấu phẩy trong trường `references`; JSON nên dùng một mảng đường dẫn.

Ví dụ JSON:

```json
{
	"defaults": {"aspect": "16:9"},
	"scenes": [
		{
			"id": "scene-001",
			"image_prompt": "Minh đứng bên chiếc xe máy trong con hẻm lúc bình minh",
			"video_prompt": "Slow dolly-in, Minh turns toward the camera",
			"references": [
				"assets/minh.png",
				"assets/motorbike.png",
				"assets/alley.png"
			]
		}
	]
}
```

Trong giao diện, chọn scene rồi nhấn **+ Thêm ảnh** để chọn nhiều ảnh tham chiếu một lần. Các ảnh được truyền vào lệnh tạo ảnh dưới dạng nhiều cờ `--ref`; nếu một file không tồn tại, scene sẽ lỗi trước khi gửi request để tránh tạo sai hình.

## Lệnh mà app thực thi

Mỗi cảnh được chạy theo thứ tự:

```text
gflow image t2i "IMAGE_PROMPT" --model nano2 --aspect 16:9 -n 1 --ref assets/minh.png --ref assets/motorbike.png --out output/scene-001/image
gflow video i2v --initial-frame FLOW_IMAGE_MEDIA_UUID "VIDEO_PROMPT" --project FLOW_PROJECT_ID --model omni-flash --duration 8 --aspect 16:9 --count 1 --out-dir output/scene-001/video
```

`gflow-cli` 0.79.0 hiện không có cờ `--resolution`/`--quality` trên `gflow video i2v`, nên không thể tự động nhấn một lựa chọn “720p” riêng trong giao diện Flow. Video I2V native của Flow thường được trả về ở mức 720p (theo tỷ lệ `--aspect`); app không gửi một cờ độ phân giải không được CLI hỗ trợ. Nếu log JSON trả về video 360p, đó là giới hạn/cấu hình đang được Flow áp cho tài khoản — hãy kiểm tra model/tier và giao diện Flow trước khi chạy lại.

CLI phát triển khá nhanh; khi một tham số bị bản `gflow-cli` đang cài từ chối, đọc log trong app, chạy `gflow video i2v --help`, rồi cập nhật `gflow-cli`. Mỗi cảnh chỉ được đánh dấu thành công nếu cả hai tiến trình `gflow` trả về mã 0 và ứng dụng tìm được ảnh mới để truyền vào bước video.

## Lưu ý vận hành

- Dùng thử 1 cảnh trước khi chạy hàng loạt để xác nhận đăng nhập, hạn mức ảnh, credit video và prompt.
- Không đóng cửa sổ trình duyệt Flow mà CLI dùng trong khi một cảnh đang chạy.
- App không tự retry để tránh tạo ảnh/video hoặc tiêu credit ngoài ý muốn. Cảnh lỗi có thể chạy lại bằng lần batch mới.
- Trên Windows, một số bản `gflow-cli` lỗi `UnicodeEncodeError` khi `--out` chứa dấu tiếng Việt. App chạy CLI trong thư mục tạm ASCII rồi chép video hoàn tất về thư mục output bạn chọn, vì thế bạn vẫn có thể chọn đường dẫn tiếng Việt.
