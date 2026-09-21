# 이미지 OCR 모델 직접 설치

[현재 구현·검증 상태](CURRENT_STATUS.md) · [남은 확인 사항](OPEN_ITEMS.md)

이미지 테마 업데이트는 아래 두 PaddleOCR 모델만 사용합니다.

1. [PP-OCRv5_mobile_det](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det)
2. [korean_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec)

각 페이지의 `Files and versions`에서 모든 파일을 내려받습니다. 각 모델의 파일 구조를 유지한 채 현재 실행하는 앱의 데이터 폴더 아래 `ocr_models`에 넣습니다.

데이터 폴더는 `AppPaths.for_current_user().data_dir`가 기준입니다.

- 설치본: `%LocalAppData%\KiwoomMonitor\data`
- 일반 개발 실행: 해당 프로젝트의 `data`
- `scripts/run_test_app_with_data.py`로 실행: `--data-dir`에 지정한 폴더

테스트 실행이 다른 프로젝트의 데이터를 사용하면 OCR 모델도 그 데이터 폴더에서 찾습니다. 실행 소스 폴더와 데이터 폴더를 구분하세요.

```text
현재 앱 데이터 폴더
└─ ocr_models
   ├─ PP-OCRv5_mobile_det
   │  └─ (첫 번째 모델의 모든 파일)
   └─ korean_PP-OCRv5_mobile_rec
      └─ (두 번째 모델의 모든 파일)
```

두 폴더가 모두 있으면 앱은 인터넷 모델 다운로드를 건너뛰고 이 파일만 사용합니다. 두 폴더 중 하나만 있으면 모델 버전이 섞일 수 있으므로, 두 모델을 모두 받은 뒤 앱을 다시 시작합니다.
