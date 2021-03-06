import pandas as pd
from skimage import io
import os
import torch
import numpy as np

import matplotlib.pyplot as plt
from torch.utils.data import Dataset, DataLoader

# xla 
try:
    import torch_xla.core.xla_model as xm
except:
    pass

#aug
from albumentations import (
    HorizontalFlip, VerticalFlip, IAAPerspective, ShiftScaleRotate, CLAHE, RandomRotate90,
    Transpose, ShiftScaleRotate, Blur, OpticalDistortion, GridDistortion, HueSaturationValue,
    IAAAdditiveGaussianNoise, GaussNoise, MotionBlur, MedianBlur, IAAPiecewiseAffine, RandomResizedCrop,
    IAASharpen, IAAEmboss, RandomBrightnessContrast, Flip, OneOf, Compose, Normalize, Cutout, CoarseDropout, ShiftScaleRotate, CenterCrop, Resize
)
from albumentations.pytorch import ToTensorV2

import pandas as pd
from sklearn.model_selection import StratifiedKFold   
from sklearn import metrics
from sklearn import model_selection

class CassavaDataset():
    
    def __init__(self, data_csv, data_root, transforms=None, augmentation=None):
        
        self.data_source = data_root + '/train_images/'
        self.transforms = transforms 
        self.map_classes = {}
        
        from json import load
        with open(data_root+'/label_num_to_disease_map.json') as f:
             mm = load(f)
        for key in mm.keys():
            self.map_classes[int(key)] = mm[key]
        
        self.train = data_csv
        self.train['disease'] = self.train["label"].map(self.map_classes)     
    
    def __len__(self):
        return len(self.train)
    
    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()
        
        image = self.read_img(self.train.iloc[idx, 0])
        
        if self.transforms:
            image = self.transforms(image=image)['image']
        
        #image = image.transpose((2, 0, 1))
        label = self.train.iloc[idx, 1]
        
        return image.double(), int(label)
    
    def read_img(self, img_id):
        return io.imread(self.data_source+img_id)
        
    def print_random(self, label=None):
        
        idx = np.random.randint(low=0, high=self.__len__())
        img, label = self.__getitem__(idx)
        print(str(label), self.map_classes[label])
        print('Image shape:', img.shape, '. Therefore, unable to print.')
        #plt.imshow(img)
        
class TestDataset():
    
    def __init__(self, data_root, transforms=None):
        
        self.data_source = data_root + '/test_images/'
        self.transforms = transforms   
        self.test = os.listdir(self.data_source)
    
    def __len__(self):
        return len(self.test)
    
    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()

        image = self.read_img(self.test[idx])
        if self.transforms:
            image = self.transforms(image=image)['image']
        return image
    
    def read_img(self, img_id):
        return io.imread(self.data_source+img_id)
        
    def print_random(self):
        from random import choice
        img = self.read_img(choice(self.test))
        plt.imshow(img)

class Data:

    def __init__(self, data_root, num_workers, bs=8, debug=False, sampler=None, transforms=None, fold=0, num_folds=5, img_size=256, tpu=False):
        self.tpu = tpu
        self.data_root = data_root
        self.fold = fold
        self.num_workers = num_workers
        self.num_folds = num_folds
        self.img_size = img_size
        self.bs = bs
        self.debug = debug
        if transforms is None:
            self.transforms = self.get_default_transform()
        else:
            self.transforms = transforms
        self.ds = self.get_ds()
        if sampler is None:
            self.sampler = self.get_default_sampler()
        else:
            self.sampler = sampler
        self.dl = None

    def get_default_sampler(self):
        if self.tpu:
            sampler = {
                'train' : torch.utils.data.distributed.DistributedSampler(
                    self.ds['train'],
                    num_replicas=xm.xrt_world_size(), #divide dataset among this many replicas
                    rank=xm.get_ordinal(), #which replica/device/core
                    shuffle=True),
                'val' : torch.utils.data.distributed.DistributedSampler(
                    self.ds['val'],
                    num_replicas=xm.xrt_world_size(),
                    rank=xm.get_ordinal(),
                    shuffle=False)
            }
        else:
            sampler = {
                'train' : None,
                'val' : None
            }
        return sampler

    
    def get_default_transform(self):
        t = {
            'train' : Compose([
                RandomResizedCrop(self.img_size, self.img_size),
                Transpose(p=0.5),
                HorizontalFlip(p=0.5),
                VerticalFlip(p=0.5),
                ShiftScaleRotate(p=0.5),
                HueSaturationValue(hue_shift_limit=0.2, sat_shift_limit=0.2, val_shift_limit=0.2, p=0.5),
                RandomBrightnessContrast(brightness_limit=(-0.1,0.1), contrast_limit=(-0.1, 0.1), p=0.5),
                Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225], max_pixel_value=255.0, p=1.0),
                CoarseDropout(p=0.5),
                Cutout(p=0.5),
                ToTensorV2(p=1.0),
            ], p=1.),
            'val' : Compose([
                Resize(int(self.img_size*1.1), int(self.img_size*1.1)),
                CenterCrop(self.img_size, self.img_size, p=1.),
                Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225], max_pixel_value=255.0, p=1.0),
                ToTensorV2(p=1.0),
            ], p=1.)
            }
        return t
    
    def get_ds(self):
        train = pd.read_csv(self.data_root+'/train.csv')
        k_fold = StratifiedKFold(n_splits=self.num_folds).split(train, train['label'])
        train_idx, val_idx = list(k_fold)[self.fold]
        if self.debug:
            train_idx = val_idx 

        ds = {
            'train' : CassavaDataset(train.loc[train_idx,:], self.data_root, transforms=self.transforms['train']),
            'val' : CassavaDataset(train.loc[val_idx,:], self.data_root, transforms=self.transforms['val']),
            'test' : TestDataset(self.data_root, transforms=self.transforms['val'])
        }
        return ds
    
    def get_dl(self):
        dl = {
            'train': torch.utils.data.DataLoader(
                self.ds['train'],
                batch_size=self.bs,
                sampler=self.sampler['train'],
                num_workers=self.num_workers,
                drop_last=True),
            'val' :  torch.utils.data.DataLoader(
                self.ds['val'],
                batch_size=self.bs,
                sampler=self.sampler['val'],
                num_workers=self.num_workers,
                drop_last=False)
        }
        self.dl = dl
        return self.dl

    def cleanup(self):
        pass
    
