import asyncio
import os
from typing import List
from PIL import Image

# Import imagehash, handle if not installed
try:
    import imagehash
except ImportError:
    imagehash = None
try:
    import cv2
except ImportError:
    cv2 = None

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, PhotoEntity

class ImageHashModule(BaseModule):
    name = "ImageHash"
    description = "Generate perceptual hashes for images"
    version = "1.0.0"
    accepts = [EntityType.PHOTO]
    produces = [EntityType.PHOTO]
    rate_limit = 0.0
    
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        file_path = entity.value
        
        if not imagehash:
            self.logger.error("imagehash library not installed")
            return results
            
        if not os.path.exists(file_path):
            return results
            
        try:
            # Run hash generation in a thread to prevent blocking
            def compute_hashes():
                with Image.open(file_path) as img:
                    phash = str(imagehash.phash(img))
                    dhash = str(imagehash.dhash(img))
                    whash = str(imagehash.whash(img))
                face_count = 0
                if cv2 is not None:
                    pixels = cv2.imread(file_path)
                    if pixels is not None:
                        gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
                        cascade = cv2.CascadeClassifier(
                            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                        )
                        face_count = len(cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5))
                return phash, dhash, whash, face_count
                    
            phash_val, dhash_val, whash_val, face_count = await asyncio.to_thread(compute_hashes)
            
            # Create a new PhotoEntity with hashes in metadata and phash field
            photo_ent = PhotoEntity(
                entity_type=EntityType.PHOTO,
                value=file_path,
                normalized_value=file_path.lower().strip(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=1.0,
                metadata={
                    "phash": phash_val,
                    "dhash": dhash_val,
                    "whash": whash_val,
                    "face_count": face_count,
                },
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                file_path=file_path,
                phash=phash_val,
                dhash=dhash_val,
                whash=whash_val,
                face_count=face_count,
            )
            results.append(photo_ent)
            
        except Exception as e:
            self.logger.error(f"Image hash generation failed for {file_path}: {e}")
            
        return results

    def is_available(self) -> bool:
        return imagehash is not None
