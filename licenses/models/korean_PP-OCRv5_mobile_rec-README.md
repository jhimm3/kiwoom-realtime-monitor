---
license: apache-2.0
library_name: PaddleOCR
language:
- en
- zh
- ko
pipeline_tag: image-to-text
tags:
- OCR
- PaddlePaddle
- PaddleOCR
- textline_recognition
---

# PP-OCRv5_server_rec

## Introduction

korean_PP-OCRv5_mobile_rec is one of the PP-OCRv5_rec that are the latest generation text line recognition models developed by PaddleOCR team. It aims to efficiently and accurately support the recognition of Korean. The key accuracy metrics are as follow:

| 模型 | 韩语数据集 精度 (%) |
|-|-|
| korean_PP-OCRv5_mobile_rec | 88.0|


**Note**: If any character (including punctuation) in a line was incorrect, the entire line was marked as wrong. This ensures higher accuracy in practical applications.

## Quick Start

### Installation

1. PaddlePaddle

Please refer to the following commands to install PaddlePaddle using pip:

```bash
# for CUDA11.8
python -m pip install paddlepaddle-gpu==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/

# for CUDA12.6
python -m pip install paddlepaddle-gpu==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/

# for CPU
python -m pip install paddlepaddle==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
```

For details about PaddlePaddle installation, please refer to the [PaddlePaddle official website](https://www.paddlepaddle.org.cn/en/install/quick).

2. PaddleOCR

Install the latest version of the PaddleOCR inference package from PyPI:

```bash
python -m pip install paddleocr
```

### Model Usage

You can quickly experience the functionality with a single command:

```bash
paddleocr text_recognition \
    --model_name korean_PP-OCRv5_mobile_rec \
    -i https://cdn-uploads.huggingface.co/production/uploads/681c1ecd9539bdde5ae1733c/g-QlJbFcFy6VQ8hQJK3SD.jpeg
```

You can also integrate the model inference of the text recognition module into your project. Before running the following code, please download the sample image to your local machine.

```python
from paddleocr import TextRecognition
model = TextRecognition(model_name="korean_PP-OCRv5_mobile_rec")
output = model.predict(input="g-QlJbFcFy6VQ8hQJK3SD.jpeg", batch_size=1)
for res in output:
    res.print()
    res.save_to_img(save_path="./output/")
    res.save_to_json(save_path="./output/res.json")
```

After running, the obtained result is as follows:

```json
{'res': {'input_path': '/root/.paddlex/predict_input/g-QlJbFcFy6VQ8hQJK3SD.jpeg', 'page_index': None, 'rec_text': '리에 씨가 자기는돈이 많이있는다고 했어요', 'rec_score': 0.9142155051231384}}
```

The visualized image is as follows:

![image/jpeg](https://cdn-uploads.huggingface.co/production/uploads/681c1ecd9539bdde5ae1733c/zFZuhWQKt5KliZ4nsR_yC.png)

For details about usage command and descriptions of parameters, please refer to the [Document](https://paddlepaddle.github.io/PaddleOCR/latest/en/version3.x/module_usage/text_recognition.html#iii-quick-start).

### Pipeline Usage

The ability of a single model is limited. But the pipeline consists of several models can provide more capacity to resolve difficult problems in real-world scenarios.

#### PP-OCRv5

The PP-OCRv5 pipeline is used to solve text recognition tasks by extracting text information from images and outputting it in string format. And there are 5 modules in the pipeline: 
* Document Image Orientation Classification Module (Optional)
* Text Image Unwarping Module (Optional)
* Text Line Orientation Classification Module (Optional)
* Text Detection Module
* Text Recognition Module

Run a single command to quickly experience the OCR pipeline:

```bash
paddleocr ocr -i https://cdn-uploads.huggingface.co/production/uploads/681c1ecd9539bdde5ae1733c/rZK0SpYaWPbYsh2UBmRqV.png \
    --text_recognition_model_name korean_PP-OCRv5_mobile_rec \
    --use_doc_orientation_classify False \
    --use_doc_unwarping False \
    --use_textline_orientation True \
    --save_path ./output \
    --device gpu:0 
```

Results are printed to the terminal:

```json
{'res': {'input_path': '/root/.paddlex/predict_input/rZK0SpYaWPbYsh2UBmRqV.png', 'page_index': None, 'model_settings': {'use_doc_preprocessor': True, 'use_textline_orientation': True}, 'doc_preprocessor_res': {'input_path': None, 'page_index': None, 'model_settings': {'use_doc_orientation_classify': False, 'use_doc_unwarping': False}, 'angle': -1}, 'dt_polys': array([[[ 15,   9],
        ...,
        [ 15,  27]],

       ...,

       [[  7, 162],
        ...,
        [  7, 187]]], dtype=int16), 'text_det_params': {'limit_side_len': 64, 'limit_type': 'min', 'thresh': 0.3, 'max_side_limit': 4000, 'box_thresh': 0.6, 'unclip_ratio': 1.5}, 'text_type': 'general', 'textline_orientation_angles': array([0, ..., 0]), 'text_rec_score_thresh': 0.0, 'rec_texts': ['보기', '저는오늘오후에 도서관에서 한국어 공부를 합니다영화를안 봅니다공부를 하기 전에 밥을', '먹습니다공부를한후에공원에서산책을 합니다민호 씨는백화점에서 쇼핑을 합니다오늘', '오후에 운동을 안 합니다.쇼핑을 하기 전에 은행에서 돈을 찾습니다쇼핑을 한 후에 식당에서', '밥을 먹습니다.'], 'rec_scores': array([0.99935752, ..., 0.96095514]), 'rec_polys': array([[[ 15,   9],
        ...,
        [ 15,  27]],

       ...,

       [[  7, 162],
        ...,
        [  7, 187]]], dtype=int16), 'rec_boxes': array([[ 15, ...,  27],
       ...,
       [  7, ..., 187]], dtype=int16)}}
```

If save_path is specified, the visualization results will be saved under `save_path`. The visualization output is shown below:

![image/jpeg](https://cdn-uploads.huggingface.co/production/uploads/681c1ecd9539bdde5ae1733c/ubJOSahsgWs1bpRXmqyPA.png)

The command-line method is for quick experience. For project integration, also only a few codes are needed as well:

```python
from paddleocr import PaddleOCR  

ocr = PaddleOCR(
    text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
    use_doc_orientation_classify=False, # Use use_doc_orientation_classify to enable/disable document orientation classification model
    use_doc_unwarping=False, # Use use_doc_unwarping to enable/disable document unwarping module
    use_textline_orientation=True, # Use use_textline_orientation to enable/disable textline orientation classification model
    device="gpu:0", # Use device to specify GPU for model inference
)
result = ocr.predict("https://cdn-uploads.huggingface.co/production/uploads/681c1ecd9539bdde5ae1733c/rZK0SpYaWPbYsh2UBmRqV.png")  
for res in result:  
    res.print()  
    res.save_to_img("output")  
    res.save_to_json("output")
```

The default model used in pipeline is `PP-OCRv5_server_rec`, and you can also use the local model file by argument `text_recognition_model_dir`. For details about usage command and descriptions of parameters, please refer to the [Document](https://paddlepaddle.github.io/PaddleOCR/latest/en/version3.x/pipeline_usage/OCR.html#2-quick-start).

## Links

[PaddleOCR Repo](https://github.com/paddlepaddle/paddleocr)

[PaddleOCR Documentation](https://paddlepaddle.github.io/PaddleOCR/latest/en/index.html)