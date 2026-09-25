import unittest
from pathlib import Path

from gflow_veo_batcher import read_scenes


class SceneParserTests(unittest.TestCase):
    def test_detects_scene_heading_with_markdown_bullets(self):
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

        file_path = Path("tmp_scene_parser_test.txt")
        file_path.write_text(text, encoding="utf-8")
        try:
            scenes, _ = read_scenes(file_path)

            self.assertEqual(len(scenes), 2)
            self.assertEqual(scenes[0].id, "scene-056")
            self.assertIn("Phân cảnh 56", scenes[0].image_prompt)
            self.assertIn("Cheers to our wonderful family day!", scenes[0].video_prompt)
            self.assertIn("Phân cảnh 57", scenes[1].image_prompt)
        finally:
            file_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
