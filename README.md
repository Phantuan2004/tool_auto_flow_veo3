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

Nút **Đăng nhập Flow** luôn chạy `gflow auth login` và mở trình duyệt để bạn chọn hoặc chuyển sang tài khoản Google Flow/Veo khác. Có thể dùng nút này trước khi tạo batch; nếu chưa đăng nhập, bước tạo ảnh/video vẫn tự mở luồng đăng nhập như trước. Nút **Đăng xuất Flow** xóa profile và cookie Flow hiện tại sau khi xác nhận; Project ID trong giao diện cũng được xóa để tránh dùng nhầm project của tài khoản cũ.

## Chạy ứng dụng

```powershell
.\.venv\Scripts\Activate.ps1
python -m py_hot_reload --ignore-venv-and-python-lib .\main.py
```

Lệnh trên sẽ tự khởi động lại ứng dụng khi bạn sửa mã nguồn. Khi cần chạy một
lần để kiểm tra nhanh, vẫn có thể dùng `python .\main.py`.

Giao diện mới chia thành hai cột:

- Bên trái là **Media Gallery**, xem ảnh đã tạo, đường dẫn video và nút mở file theo scene.
- Bên phải là **Batch Configuration**, chọn kịch bản/thư mục output, Project ID, tỷ lệ, danh sách scene và chỉnh prompt ảnh/video.
- Thanh công cụ có nút kiểm tra `gflow`, tạo ảnh, tạo video và dừng tiến trình.
- Dry-run được bật mặc định để kiểm tra luồng mà không tiêu credit.

Không cần chạy trực tiếp `gflow_veo_batcher.py`; file đó giữ phần backend tương thích với phiên bản cũ.

## Định dạng tệp cảnh

Hỗ trợ JSON, JSONL/NDJSON, CSV/TSV, TXT và Markdown. TXT/Markdown có thể là một prompt trên mỗi dòng hoặc dạng kịch bản có cấu trúc gần JSON như sau:

```text
Phân cảnh 49 (Nhân vật hoạt hình 3D): Trong phòng tắm gia đình...

**Phong cách hình ảnh:** 3D style animation, highly detailed.
**Bối cảnh:** Trong nhà, bồn tắm đầy nước.
**Chỉ đạo quay & Chuyển động:**
- **Góc quay:** Cận cảnh Henry chà lưng cho Dad.
**Lời thoại:**
- **Henry:** "Scrub back! Big back!"
**Negative Prompt:** No logos, no subtitles, no watermark.
```

Mỗi tiêu đề `Phân cảnh N` tạo thành một scene. Ứng dụng tự ghép mô tả, phong cách, ánh sáng, bối cảnh, trang phục, nhân vật và biểu cảm vào prompt ảnh; chỉ đạo quay, chuyển động và lời thoại được thêm vào prompt video. Các phần không có trong mẫu vẫn được chấp nhận.

Với JSON, các trường dùng được:

Với JSON, danh sách phân cảnh có thể đặt trực tiếp dưới dạng mảng hoặc trong một trong các khóa `scenes`, `shots`, `items`, `data`. File chỉ có một cảnh cũng được hỗ trợ nếu là object chứa `prompt`, `image_prompt` hoặc `video_prompt`, hoặc đặt object đó trong khóa `scene`. Ví dụ tối thiểu:

```json
{
	"defaults": {"aspect": "16:9"},
	"shots": [
		{"id": "intro", "prompt": "A cinematic opening shot"},
		{"id": "close-up", "image_prompt": "A detailed close-up", "video_prompt": "Slow camera push-in"}
	]
}
```

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

Trong giao diện, chọn scene rồi nhấn **+ Thêm ảnh** để chọn nhiều ảnh tham chiếu một lần. Bộ ảnh tham chiếu của file/batch được dùng chung cho mọi scene, vì vậy tất cả các phân cảnh đều nhận cùng toàn bộ ảnh gốc khi tạo ảnh. Các ảnh được truyền vào lệnh tạo ảnh dưới dạng nhiều cờ `--ref`; nếu một file không tồn tại, scene sẽ lỗi trước khi gửi request để tránh tạo sai hình. Thay đổi danh sách ảnh trong giao diện cũng được đồng bộ cho toàn bộ scene.

## Lệnh mà app thực thi

Mỗi cảnh được chạy theo thứ tự:

```text
gflow image i2i "IMAGE_PROMPT" --model nano2 --aspect 16:9 -n 1 --ref assets/minh.png --ref assets/motorbike.png --project FLOW_PROJECT_ID --out output/scene-001/image
gflow video i2v --initial-frame FLOW_IMAGE_MEDIA_UUID "VIDEO_PROMPT" --project FLOW_PROJECT_ID --model omni-flash --duration 8 --aspect 16:9 --count 1 --out-dir output/scene-001/video
```

Scene không có ảnh tham chiếu vẫn dùng `gflow image t2i`. Scene có ảnh tham chiếu dùng `gflow image i2i`; `gflow-cli` sẽ tải từng đường dẫn local trong các cờ `--ref` lên Flow và dùng các media đó làm reference trong cùng lần tạo ảnh.
Nano Banana 2 nhận tối đa 10 ảnh tham chiếu cho một scene; app sẽ báo lỗi trước khi upload nếu vượt quá giới hạn này.

`gflow-cli` 0.79.0 hiện không có cờ `--resolution`/`--quality` trên `gflow video i2v`, nên không thể tự động nhấn một lựa chọn “720p” riêng trong giao diện Flow. Video I2V native của Flow thường được trả về ở mức 720p (theo tỷ lệ `--aspect`); app không gửi một cờ độ phân giải không được CLI hỗ trợ. Nếu log JSON trả về video 360p, đó là giới hạn/cấu hình đang được Flow áp cho tài khoản — hãy kiểm tra model/tier và giao diện Flow trước khi chạy lại.

CLI phát triển khá nhanh; khi một tham số bị bản `gflow-cli` đang cài từ chối, đọc log trong app, chạy `gflow video i2v --help`, rồi cập nhật `gflow-cli`. Mỗi cảnh chỉ được đánh dấu thành công nếu cả hai tiến trình `gflow` trả về mã 0 và ứng dụng tìm được ảnh mới để truyền vào bước video.

## Lưu ý vận hành

- Dùng thử 1 cảnh trước khi chạy hàng loạt để xác nhận đăng nhập, hạn mức ảnh, credit video và prompt.
- Không đóng cửa sổ trình duyệt Flow mà CLI dùng trong khi một cảnh đang chạy.
- App không tự retry để tránh tạo ảnh/video hoặc tiêu credit ngoài ý muốn. Cảnh lỗi có thể chạy lại bằng lần batch mới.
- Trên Windows, một số bản `gflow-cli` lỗi `UnicodeEncodeError` khi `--out` chứa dấu tiếng Việt. App chạy CLI trong thư mục tạm ASCII rồi chép video hoàn tất về thư mục output bạn chọn, vì thế bạn vẫn có thể chọn đường dẫn tiếng Việt.
- Toàn bộ lệnh, output CLI và lỗi trong quá trình đăng nhập, tạo project, tạo ảnh hoặc tạo video được ghi nối tiếp vào `output/_logs/gflow_errors.log` của thư mục output đang chọn.
- Nếu cài `ffmpeg` và có trong `PATH`, app tự remux MP4/MOV với `+faststart` trước khi chép ra output để tăng khả năng phát trên Windows. Không có `ffmpeg`, app vẫn giữ video gốc và ghi cảnh báo vào log.
