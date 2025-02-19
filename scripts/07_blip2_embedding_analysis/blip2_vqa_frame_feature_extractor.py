import os
import cv2
import torch
import numpy as np
from PIL import Image
from transformers import (
    Blip2Processor,
    Blip2ForConditionalGeneration,
)
from sentence_transformers import SentenceTransformer
from frame_feature_extractor import FrameFeatureExtractor

from typing import List


class BLIP2VQAFrameFeatureExtractor(FrameFeatureExtractor):
    def __init__(self, args):
        super().__init__(args=args)
        self.args = args
        self.processor = Blip2Processor.from_pretrained(
            os.path.join(os.environ["SCRATCH"], "mq_libs/blip2-flan-t5-xl")
        )

        if torch.cuda.device_count() > 1:
            device_map = {
                "query_tokens": 0,
                "vision_model": 0,
                "language_model": 1,
                "language_projection": 1,
                "qformer": 0,
            }
        else:
            device_map = "auto"

        self.blip2_vqa_model = Blip2ForConditionalGeneration.from_pretrained(
            os.path.join(os.environ["SCRATCH"], "mq_libs/blip2-flan-t5-xl"),
            device_map=device_map,
            torch_dtype=torch.float16,
        )
        self.sbert_model = SentenceTransformer(
            "sentence-transformers/all-distilroberta-v1", device="cuda:0"
        )  # cuda:0
        self.question = "Question: What is the person in this picture doing? Answer:"

    def get_new_input(self, current_embedding_index: int, cap: cv2.VideoCapture):
        # original_fps = cap.get(cv2.CAP_PROP_FPS)
        # if self.number_of_frames_per_input > 1:
        #     cursor_stride_in_same_window = int(
        #         np.round(original_fps / float(self.target_fps))
        #     )
        #     current_input_center_frame_index = int(
        #         np.round(
        #             current_input_start_frame_index
        #             + original_fps
        #             / float(self.target_fps)
        #             * self.number_of_frames_per_input
        #             / 2
        #         )
        #     )
        # else:

        frame_index = int(
            current_embedding_index * cap.get(cv2.CAP_PROP_FRAME_COUNT) / 1024.0
        )

        current_input = {
            "frame_index": frame_index,
            "frames": [],
        }

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index - 1)
        success, frame = cap.read()  # (HWC, BGR)
        # if self.number_of_frames_per_input > 1:
        #     current_cursor += cursor_stride_in_same_window
        if not success:
            return None, None

        current_input["frames"].append(frame)

        current_embedding_index += 1
        return current_embedding_index, current_input

    def predictor_function(
        self, frame_index: int, frames: List[np.array]  # (T, H, C, W), np.uint8, BGR
    ):
        if len(frames) != 1:
            raise Exception("Number of frames should be 1.")
        with torch.no_grad():
            model_input = self.processor(
                images=[Image.fromarray(frames[0][:, :, ::-1])],
                text=[self.question],
                return_tensors="pt",
            ).to(self.blip2_vqa_model.device)
            results = self.blip2_vqa_model.generate(**model_input)
            caption_token_ids = results.pop("caption_token_ids")
            results["caption"] = self.processor.batch_decode(
                caption_token_ids, skip_special_tokens=True
            )[0].strip()

            caption_sbert_embedding = self.sbert_model.encode([results["caption"]])[
                0
            ].ravel()
            encoder_output = results["encoder_output"]
            return (
                caption_sbert_embedding,
                encoder_output,
            )

    @staticmethod
    def save_results(
        caption_sbert_embeddings,
        encoder_outputs,
        output_folder_path,
        clip_uid,
    ):
        os.makedirs(
            os.path.join(output_folder_path, "caption_sbert_embeddings"),
            exist_ok=True,
        )

        os.makedirs(
            os.path.join(output_folder_path, "encoder_outputs"),
            exist_ok=True,
        )

        torch.save(
            caption_sbert_embeddings,
            os.path.join(
                output_folder_path, "caption_sbert_embeddings", clip_uid + ".pt"
            ),
        )

        torch.save(
            encoder_outputs,
            os.path.join(output_folder_path, "encoder_outputs", clip_uid + ".pt"),
        )
