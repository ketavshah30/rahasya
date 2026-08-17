import asyncio
from typing import List
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import os

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, PhotoEntity, LocationEntity

class ExifModule(BaseModule):
    name = "ExifData"
    description = "Extract EXIF and GPS metadata from images"
    version = "1.0.0"
    accepts = [EntityType.PHOTO]
    produces = [EntityType.LOCATION, EntityType.PHOTO]
    rate_limit = 0.0
    
    def _convert_to_degrees(self, value):
        d, m, s = value
        return float(d) + (float(m) / 60.0) + (float(s) / 3600.0)
        
    def _get_exif_data(self, image):
        exif_data = {}
        info = image._getexif()
        if info:
            for tag, value in info.items():
                decoded = TAGS.get(tag, tag)
                if decoded == "GPSInfo":
                    gps_data = {}
                    for t in value:
                        sub_decoded = GPSTAGS.get(t, t)
                        gps_data[sub_decoded] = value[t]
                    exif_data[decoded] = gps_data
                else:
                    if isinstance(value, bytes):
                        try:
                            value = value.decode('utf-8')
                        except:
                            value = str(value)
                    exif_data[decoded] = value
        return exif_data

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        file_path = entity.value
        
        if not os.path.exists(file_path):
            return results
            
        try:
            with Image.open(file_path) as img:
                exif = self._get_exif_data(img)
                
                # Update original entity if it's a PhotoEntity or create a new one
                gps_info = exif.get("GPSInfo", {})
                lat = None
                lon = None
                
                if gps_info:
                    gps_lat = gps_info.get("GPSLatitude")
                    gps_lat_ref = gps_info.get("GPSLatitudeRef")
                    gps_lon = gps_info.get("GPSLongitude")
                    gps_lon_ref = gps_info.get("GPSLongitudeRef")
                    
                    if gps_lat and gps_lat_ref and gps_lon and gps_lon_ref:
                        lat = self._convert_to_degrees(gps_lat)
                        if gps_lat_ref != "N":
                            lat = -lat
                        lon = self._convert_to_degrees(gps_lon)
                        if gps_lon_ref != "E":
                            lon = -lon
                            
                # Create detailed PhotoEntity
                photo_ent = PhotoEntity(
                    entity_type=EntityType.PHOTO,
                    value=file_path,
                    normalized_value=file_path.lower().strip(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=1.0,
                    metadata={"exif": exif},
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    file_path=file_path,
                    exif_data=exif,
                    gps_coords=f"{lat},{lon}" if lat and lon else None
                )
                results.append(photo_ent)
                
                if lat and lon:
                    loc_ent = LocationEntity(
                        entity_type=EntityType.LOCATION,
                        value=f"{lat},{lon}",
                        normalized_value=f"{lat},{lon}",
                        source_module=self.name,
                        source_reliability=SourceReliability.HIGH,
                        confidence=1.0,
                        metadata={"source_image": file_path},
                        parent_entity_id=photo_ent.id,
                        depth=photo_ent.depth + 1,
                        latitude=lat,
                        longitude=lon,
                        source_type="EXIF"
                    )
                    results.append(loc_ent)
                    
        except Exception as e:
            self.logger.error(f"EXIF extraction failed for {file_path}: {e}")
            
        return results

    def is_available(self) -> bool:
        return True
