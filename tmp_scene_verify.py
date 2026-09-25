from pathlib import Path
from gflow_veo_batcher import read_scenes

text = '''*Phân cảnh 56**
* **Phong cách:** 3D style animation, highly detailed.
* **Góc quay:** Trung cảnh cả gia đình 5 người cùng giơ ly nước trái cây lên giữa bàn -> Góc cận cảnh những chiếc ly chạm nhau lách cách nhẹ nhàng -> Góc lia qua từng nụ cười rạng rỡ của gia đình.
* **Ánh sáng:** 5600K, ánh sáng tông lạnh dịu mát, phủ đều không gian, không chói gắt, không phản chiếu.
* **Bối cảnh:** Bàn ăn gia đình ấm cúng tại công viên nước.
* **Nhân vật xuất hiện:** Dad, Mom, Jace, Maya, Henry
* **Hành động:** Cả nhà cùng nâng ly nước cam và nước táo mát lạnh chạm ly chúc mừng một chuyến đi chơi công viên nước tràn ngập niềm vui và bài học bổ ích.
* **Lời thoại nhân vật:** 
  * Maya: "Cheers to our wonderful family day!"
  * Henry: "Cheers! Big happy!"
* Note: no text, no title

*Phân cảnh 57**
* **Phong cách:** 2D anime style.
'''

path = Path('tmp_scene_parser_test.txt')
path.write_text(text, encoding='utf-8')
scenes, _ = read_scenes(path)
print(f'count={len(scenes)}')
print(f'first_id={scenes[0].id}')
print(f'quote={"Cheers to our wonderful family day!" in scenes[0].video_prompt}')
print(f'second_scene={"Phân cảnh 57" in scenes[1].image_prompt}')
path.unlink(missing_ok=True)
