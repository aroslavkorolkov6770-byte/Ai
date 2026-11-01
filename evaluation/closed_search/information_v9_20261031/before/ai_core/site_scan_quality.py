"""Проверка видимых потерь скана. Не восстанавливает цифры и запрещающие слова."""
from __future__ import annotations
import cv2
import numpy as np
import re


def critical_key(text):
    key=text.casefold().strip('.,:;!?()[]{}«»\"\'')
    return key if re.search(r'\d',key) or re.fullmatch(r'не|нет|ни|нельзя|без|запрещ\w*',key) else None


def critical_consistent(first,second):
    """Два вида одного изображения не должны давать разные цифры/отрицания."""
    for source,target in ((first,second),(second,first)):
        for word in source:
            key=critical_key(word['text'])
            if key is None:
                continue
            if word['confidence']<80:
                return False
            matched=False
            for other in target:
                if critical_key(other['text'])!=key:
                    continue
                w=max(0,min(word['left']+word['width'],other['left']+other['width'])-max(word['left'],other['left']))
                h=max(0,min(word['top']+word['height'],other['top']+other['height'])-max(word['top'],other['top']))
                if w*h>=.25*min(word['width']*word['height'],other['width']*other['height']):
                    matched=True
                    break
            if not matched:
                return False
    return True


def normalize_lighting(image):
    """Выравниваем плавную тень по оценке бумаги, сохраняя исходные штрихи."""
    background=cv2.morphologyEx(image,cv2.MORPH_CLOSE,np.ones((61,61),np.uint8))
    low,high=np.percentile(background,[5,95])
    if high-low<50 or low>190:
        return image
    # Деление на яркость бумаги не дорисовывает отсутствующие символы.
    return cv2.divide(image,np.maximum(background,1),scale=255)


def ink_mask(image):
    return cv2.threshold(image,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)[1]


def clipped_content(image):
    """Штрихи текста/таблицы упираются в край: начало или конец строки потеряны."""
    height,width=image.shape
    margin=max(3,round(min(height,width)*.002))
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(ink_mask(image),8)
    for x,y,w,h,area in stats[1:]:
        if area<12 or h<6 or w<2:
            continue
        # Тонкая рамка самого снимка не является словом. Текст, соединённый
        # с обрезанной сеткой таблицы, наоборот, должен закрывать ответ.
        if h>height*.85 or w>width*.98 and h<margin*3:
            continue
        if x<=margin or y<=margin or x+w>=width-margin or y+h>=height-margin:
            return True
    return False


def unrecognized_ink(image,words):
    """OCR может пропустить символ целиком, не дав ему низкую уверенность."""
    ink=ink_mask(image)
    coverage=np.zeros(image.shape,np.uint8)
    height,width=image.shape
    for word in words:
        x,y=word['left'],word['top']
        coverage[max(0,y-2):min(height,y+word['height']+2),max(0,x-2):min(width,x+word['width']+2)]=1
    count,labels,stats,centroids=cv2.connectedComponentsWithStats(ink,8)
    for label,(x,y,w,h,area) in enumerate(stats[1:],1):
        if area<15 or h<7 or w<2 or w/max(h,1)>20 or h/max(w,1)>20:
            continue
        component=labels[y:y+h,x:x+w]==label
        missing=np.count_nonzero(component & (coverage[y:y+h,x:x+w]==0))
        if missing/area>.55:
            return True
    return False
