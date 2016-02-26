#!/usr/bin/env python2

from __future__ import division
from __future__ import with_statement
from __future__ import print_function

import numpy
import time
import os
import os.path

import matchmmd
from gen_deepart import minibatch
from gen_deepart import setup_classifier
from gen_deepart import deepart_reconstruct
from gen_deepart import ratelimit

def extract(S,featext,model,image_dims,device_id,blob_names):
  '''S: a list of image paths
featext: image features will be stored in files with this suffix
model: name of CNN model
image_dims: 2-tuple of height, width
device_id: GPU device id, zero indexed
blob_names: names of blobs to extract, must be in forward order
'''
  rlprint=ratelimit(interval=60)(print)
  caffe,net,image_dims=setup_classifier(model=model,image_dims=image_dims,device_id=device_id)
    
  # minibatch processing
  M=10
  work_units,work_done,work_t0=len(S),0,time.time()
  for i,inputs in enumerate(minibatch(S,M)):
    F=net.extract_features(inputs,blob_names,auto_reshape=True)
    for j,ipath in enumerate(inputs):
      opath=os.path.splitext(ipath)[0]+featext
      with open(opath,'wb') as f:
        numpy.savez(f,conv3_1=F['conv3_1'][j],conv4_1=F['conv4_1'][j],conv5_1=F['conv5_1'][j])
    work_done=work_done+len(inputs)
    rlprint('extract {}/{}, {} min remaining'.format(work_done,work_units,(work_units/work_done-1)*(time.time()-work_t0)/60.0))

def form_F(ipath,featext,blob_names):
  '''ipath: a list of paths to images
featext: image features will be read from files with this suffix
blob_names: names of blobs to read, in order

Returns F, F_slice and F_shape. Each row of F is a concatenation of the
flattened blobs. To recover blob k: F[i,F_slice[k]].reshape(*F_shape[k])
'''
  F_shape={}
  F_slice={}
  F=[]
  for i,x in enumerate(ipath):
    data=numpy.load(os.path.splitext(x)[0]+featext)
    if i==0:
      for k in blob_names:
        F_shape[k]=data[k].shape
    F.append(numpy.concatenate([data[k].ravel() for k in blob_names]))
  F=numpy.array(F)
  index=0
  for k in blob_names:
    F_slice[k]=slice(index,index+numpy.prod(F_shape[k]))
    index=index+numpy.prod(F_shape[k])
  return F,F_slice,F_shape

def run(ipath,N,M,model,image_dims,device_id,weights,rbf_var,prefix,max_iter,hybrid):
  '''This function will take a list of paths to images and run deep
manifold traversal. First, features are extracted if needed. Next,
the manifold traversal of each image is optimized. Lastly, the images
are reconstructed and visualized. Visual results appear in a directory
named result_TIMESTAMP_PREFIX.

ipath: a list of image paths
N: The first N images are P, the source
M: The next M images are Q, the target
len(X): The remaining images are X, the images to transform.
model: name of CNN model
image_dims: 2-tuple of height, width
device_id: GPU device id, zero indexed
weights: a list of lambda weights for the budget-of-change regularizer
rbf_var: variance for the rbf kernel
prefix: used in the name of the results directory
max_iter: number of iterations for the reconstruction
hybrid: True if you want to the use the layer regularizer

Returns XF, F2, root_dir and result. XF is the feature matrix of the
original images. F2 is the feature matrix of the transformed images
(len(weights) transformed images per original image). root_dir the the
name of the results directory. result is the transformed images.
'''
  rlprint=ratelimit(interval=60)(print)

  P=ipath[:N]
  Q=ipath[N:N+M]
  X=ipath[N+M:]

  print('{} source, {} target, {} test'.format(N,M,len(X)))
  assert N>0 and M>0 and len(X)>0

  blob_names=['conv3_1','conv4_1','conv5_1']

  # extract features
  featext='.{}.{}x{}.npz'.format(model,*image_dims)
  S=list(set(x for x in ipath if not os.path.exists(os.path.splitext(x)[0]+featext)))
  if len(S)>0:
    extract(S,featext,model,image_dims,device_id,blob_names)

  # Form F (first N rows are P, next M rows are Q, last row is x)
  F,F_slice,F_shape=form_F(ipath,featext,blob_names)
  print('F',F.shape)
  print(F_slice)
  print(F_shape)
  XF=F[N+M:]

  # Solve for multiple points on the manifold (move away from P toward Q)
  allF2=[]
  work_units,work_done,work_t0=len(XF),0,time.time()
  for x in XF:
    F[N+M]=x
    XPR,R=matchmmd.manifold_traversal(F[:N+M+1],N,M,weights,rbf_var=rbf_var,checkgrad=False,checkrbf=True)
    print('R',R.shape,R.dtype,R.sum(axis=1))
    allF2.append(XPR.dot(F[:N+M+1]))
    work_done=work_done+1
    rlprint('dmt {}/{}, {} min remaining'.format(work_done,work_units,(work_units/work_done-1)*(time.time()-work_t0)/60.0))
  F2=numpy.asarray(allF2,dtype=numpy.float32)
  print('F2',F2.shape,F2.dtype,F2.min(),F2.max())
  # save deep manifold traversal result
  #with open('{}_dmt.npz'.format(prefix),'wb') as f: numpy.savez(f,PF=F[:N],QF=F[N:N+M],XF=XF,F2=F2,XPR=XPR,R=R,F_shape=F_shape,weights=weights,rbf_var=rbf_var)

  F2=F2.reshape(F2.shape[0]*F2.shape[1],-1)
  dataset_F=numpy.concatenate([XF,F2],axis=0)
  data_indices=range(len(X),len(X)+len(F2))
  test_indices=list(numpy.repeat(range(len(X)),len(weights)))
 
  if hybrid:
    root_dir,result=deepart_reconstruct(blob_names=blob_names,blob_weights=[1]*len(blob_names),prefix=prefix,max_iter=max_iter,test_indices=test_indices,data_indices=data_indices,image_dims=image_dims,hybrid_names=['conv1_1','conv2_1'],hybrid_weights=[0.02,0.02],dataset=X,dataset_F=dataset_F,dataset_slice=F_slice,dataset_shape=F_shape,desc=prefix)
  else:
    root_dir,result=deepart_reconstruct(blob_names=blob_names,blob_weights=[1]*len(blob_names),prefix=prefix,max_iter=max_iter,test_indices=test_indices,data_indices=data_indices,image_dims=image_dims,dataset=X,dataset_F=dataset_F,dataset_slice=F_slice,dataset_shape=F_shape,desc=prefix)

  return XF,F2,root_dir,result