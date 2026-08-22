# 이미지 OCR 모델 직접 설치

이미지 테마 업데이트는 아래 두 PaddleOCR 모델만 사용합니다.

1. [PP-OCRv5_mobile_det](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det)
2. [korean_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec)

각 페이지의 `Files and versions`에서 모든 파일을 내려받습니다. 각 모델의 파일 구조를 유지한 채 아래 위치에 넣습니다.

```text
프로젝트 폴더
└─ data
   └─ ocr_models
      ├─ PP-OCRv5_mobile_det
      │  └─ (첫 번째 모델의 모든 파일)
      └─ korean_PP-OCRv5_mobile_rec
         └─ (두 번째 모델의 모든 파일)
```

두 폴더가 모두 있으면 앱은 인터넷 모델 다운로드를 건너뛰고 이 파일만 사용합니다. 두 폴더 중 하나만 있으면 모델 버전이 섞일 수 있으므로, 두 모델을 모두 받은 뒤 앱을 다시 시작합니다.
