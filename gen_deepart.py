#!/usr/bin/env python2

from __future__ import division
from __future__ import with_statement
from __future__ import print_function

import numpy as np
import inspect
import ast
import yaml
import sys
skimage_io_import_bug_workaround=sys.argv
try:
  sys.argv=sys.argv[:1]
  import skimage.io
finally:
  sys.argv=skimage_io_import_bug_workaround
  del skimage_io_import_bug_workaround
import skimage.restoration
import time
import glob
import os
import os.path
import subprocess
import pipes
import h5py
import itertools
import scipy

from fet_extractor import load_fet_extractor
from deepart import gen_target_data, optimize_img
from test_deepart import test_all_gradients
import measure
import deepart

def ratelimit(n=0,interval=0.0,timefn=time.time,blocking=False,blockingfn=time.sleep):
  def d(f):
    count=[n,interval,timefn,blockingfn]
    if interval>0.0: count[1]=count[2]()-interval
    def c(*args,**kwds):
      if n>0: count[0]=count[0]+1
      t=count[1]
      if interval>0.0: t=count[2]()
      if blocking and interval>t-count[1]:
        count[3](interval-(t-count[1]))
        t=interval+count[1]
      if count[0]>=n and t>=interval+count[1]:
        count[0]=0
        count[1]=t
        f(*args,**kwds)
    return c
  return d

def minibatch(x,n):
  it=iter(x)
  while True:
    x=list(itertools.islice(it,n))
    if len(x)<1: break
    yield x

def filter_args(args,valid_args,help_args,depth=1):
  caller_globals=inspect.stack()[depth][0].f_globals
  if '--help' in args:
    for x in valid_args:
      print('--{:30} (Default: {}){}'.format(x,caller_globals[x],'' if x not in help_args else ' '+help_args[x]))
    sys.exit(1)
  result=[]
  for x in args:
    if not x.startswith('--'):
      result.append(x)
      continue
    if '=' not in x:
      k=x[2:]
      if k not in valid_args:
        print('Unknown option {}'.format('--'+k))
        sys.exit(1)
      caller_globals[k]=True
    else:
      k,v=x.split('=',1)
      k=k[2:]
      if k not in valid_args:
        print('Unknown option {}'.format('--'+k))
        sys.exit(1)
      if isinstance(caller_globals[k],str):
        caller_globals[k]=v
      elif isinstance(caller_globals[k],tuple):
        try:
          caller_globals[k]=tuple(ast.literal_eval(v))
        except:
          # fallback to yaml, it can handle strings without quotes
          caller_globals[k]=tuple(yaml.load(v))
      elif isinstance(caller_globals[k],list):
        try:
          caller_globals[k]=list(ast.literal_eval(v))
        except:
          # fallback to yaml, it can handle strings without quotes
          caller_globals[k]=list(yaml.load(v))
      else:
        try:
          caller_globals[k]=ast.literal_eval(v) # QQQ big problem here: literal_eval performs arithmetic e.g., 2011-11-11 becomes the integer 1989
        except:
          # this does not handle nested strings properly
          caller_globals[k]=v
  return result

def setup_classifier(model='vgg',image_dims=(224,224),device_id=1):
    #deployfile_relpath = 'models/VGG_CNN_19/VGG_ILSVRC_19_layers_deploy_deepart.prototxt'
    #weights_relpath = 'models/VGG_CNN_19/VGG_ILSVRC_19_layers.caffemodel'
    #image_dims = (1014//2, 1280//2)
    #mean = (104, 117, 123)

    if model=='vgg':
        deployfile_relpath = 'models/VGG_CNN_19/VGG_ILSVRC_19_layers_deploy_fullconv.prototxt'
        weights_relpath = 'models/VGG_CNN_19/vgg_normalised.caffemodel'
        mean = (103.939, 116.779, 123.68)
    elif model=='vggface':
        deployfile_relpath = 'models/vgg_face_caffe/VGG_FACE_deploy_conv.prototxt'
        weights_relpath = 'models/vgg_face_caffe/VGG_FACE.caffemodel'
        mean = (93.5940, 104.7624, 129.1863)
    else:
        raise ValueError('Unknown CNN model:',model)
    input_scale = 1.0

    caffe, net = load_fet_extractor(
        deployfile_relpath, weights_relpath, image_dims, mean, device_id,
        input_scale
    )

    return caffe, net, image_dims


def run_deepart(ipath1='images/starry_night.jpg',ipath2='images/tuebingen.jpg',max_iter=2000):
    np.random.seed(123)

    root_dir = 'results_{}'.format(int(round(time.time())))
    if not os.path.exists(root_dir):
        os.makedirs(root_dir)
    display = 100
    # list of targets defined by tuples of
    # (
    #     image path,
    #     target blob names (these activations will be included in the loss function),
    #     whether we use style (gram) or content loss,
    #     weighting factor
    # )
#    targets = [
#        (ipath1, ['conv1_1', 'conv2_1', 'conv3_1', 'conv4_1', 'conv5_1'], True, 1),
#        (ipath2, ['conv4_2'], False, 1),
#    ]
#    targets = [
#        (ipath1, ['conv1_1', 'conv2_1', 'conv3_1', 'conv4_1', 'conv4_2', 'conv5_1'], False, 1),
#    ]
    targets = [
        (ipath1, ['conv5_1'], False, 1),
    ]
    # These have to be in the same order as in the network!
    all_target_blob_names = ['conv1_1', 'conv2_1', 'conv3_1', 'conv4_1', 'conv4_2', 'conv5_1']

    caffe, net, image_dims = setup_classifier()

    A=caffe.io.load_image(args[0])
    B=net.preprocess_inputs([A],auto_reshape=True)
    C=net.transformer.deprocess(net.inputs[0],B)
    D=caffe.io.resize_image(C,A.shape)
    print('input',A.shape,A.dtype,A.min(),A.max())
    print('pre',B.shape,B.dtype,B.min(),B.max())
    print('de',C.shape,C.dtype,C.min(),C.max())
    print('re',D.shape,D.dtype,D.min(),D.max())
    print('psnr = {:.4}, ssim = {:.4}'.format(measure.measure_PSNR(A,D,1).mean(),measure.measure_SSIM(A,D,1).mean()))

    # Generate activations for input images
    target_data_list = gen_target_data(root_dir, caffe, net, targets)

    # Generate white noise image
    init_img = np.random.normal(loc=0.5, scale=0.1, size=image_dims + (3,))

    solver_type = 'L-BFGS-B'
    solver_param = {}

    #test_all_gradients(init_img, net, all_target_blob_names, targets, target_data_list)

    Chat=optimize_img(
        init_img, solver_type, solver_param, max_iter, display, root_dir, net,
        all_target_blob_names, targets, target_data_list
    )
    Dhat=caffe.io.resize_image(Chat,A.shape)
    print('best psnr = {:.4}, ssim = {:.4}'.format(measure.measure_PSNR(A,D,1).mean(),measure.measure_SSIM(A,D,1).mean()))
    print('actual psnr = {:.4}, ssim = {:.4}'.format(measure.measure_PSNR(A,Dhat,1).mean(),measure.measure_SSIM(A,Dhat,1).mean()))
    skimage.io.imsave('{}/eval_original.png'.format(root_dir),A)
    skimage.io.imsave('{}/eval_best.png'.format(root_dir),D)
    skimage.io.imsave('{}/eval_actual.png'.format(root_dir),Dhat)
    caption='psnr {:.4}, ssim {:.4}'.format(measure.measure_PSNR(A,Dhat,1).mean(),measure.measure_SSIM(A,Dhat,1).mean())
    subprocess.check_call('convert {root_dir}/eval_best.png {root_dir}/eval_actual.png -size {w}x -font Arial-Italic -pointsize 12 caption:{caption} -append {root_dir}/eval.png'.format(root_dir=pipes.quote(root_dir),caption=pipes.quote(caption),w=A.shape[1],h=A.shape[0]//10),shell=True)

def deepart2(ipath1,ipath2,init_img=None,display=100,root_dir='results',max_iter=2000):
    targets = [
        (ipath1, ['conv1_1', 'conv2_1', 'conv3_1', 'conv4_1', 'conv5_1'], True, 100),
        (ipath2, ['conv4_2'], False, 1),
    ]
    # These have to be in the same order as in the network!
    all_target_blob_names = ['conv1_1', 'conv2_1', 'conv3_1', 'conv4_1', 'conv4_2', 'conv5_1']
    caffe, net, image_dims = setup_classifier()

    # Generate activations for input images
    target_data_list = gen_target_data(root_dir, caffe, net, targets)

    # Generate white noise image
    if init_img == None:
        init_img = np.random.normal(loc=0.5, scale=0.1, size=image_dims + (3,))

    solver_type = 'L-BFGS-B'
    solver_param = {}

    optimize_img(
        init_img, solver_type, solver_param, max_iter, display, root_dir, net,
        all_target_blob_names, targets, target_data_list
    )

def deepart_identity(image_dims=(224,224),max_iter=1000):
  # Experimenting with making deepart produce the identity function
  t0=time.time()
  display = 100
  # These have to be in the same order as in the network!
  all_target_blob_names = ['conv1_1', 'conv2_1', 'conv3_1', 'conv4_1', 'conv4_2', 'conv4_3', 'conv5_1', 'conv5_2', 'conv5_3']

  #ipathset = [
  #  'images/celebrity_tr/59_Mr._T_0004.jpg',
  #  'images/celebrity_tr/56_James_Read_0014.jpg',
  #  'images/celebrity_tr/57_Stephen_Lang_0008.jpg',
  #  'images/celebrity_tr/50_Denzel_Washington_0013.jpg',
  #  'images/celebrity_tr/57_Michael_Bowen_0012.jpg',
  #  'images/celebrity_tr/61_Annie_Golden_0006.jpg',
  #  'images/celebrity_tr/55_Jamey_Sheridan_0006.jpg',
  #  'images/celebrity_tr/54_Hulk_Hogan_0007.jpg',
  #  'images/celebrity_tr/56_Gus_Van_Sant_0007.jpg',
  #  'images/celebrity_tr/57_Chuck_Lorre_0001.jpg',
  #]

  ipathset = [
    'images/lfw/Charles_Moose/Charles_Moose_0013.jpg',
    'images/lfw/Eddy_Merckx/Eddy_Merckx_0002.jpg',
    'images/lfw/Taufik_Hidayat/Taufik_Hidayat_0001.jpg',
    'images/lfw/John_Travolta/John_Travolta_0005.jpg',
    'images/lfw/Hitomi_Soga/Hitomi_Soga_0001.jpg',
    'images/lfw/Lino_Oviedo/Lino_Oviedo_0001.jpg',
    'images/lfw/Daniel_Montgomery/Daniel_Montgomery_0001.jpg',
    'images/lfw/Paul_Burrell/Paul_Burrell_0004.jpg',
    'images/lfw/David_Beckham/David_Beckham_0031.jpg',
    'images/lfw/Caroline_Dhavernas/Caroline_Dhavernas_0001.jpg',
  ]

  targetset=[
    ('c5',[ ['', ['conv5_1'], False, 1], ]),
    ('c4',[ ['', ['conv4_1'], False, 1], ]),
    ('c3',[ ['', ['conv3_1'], False, 1], ]),
    ('c45',[ ['', ['conv4_1', 'conv5_1'], False, 1], ]),
    ('c345',[ ['', ['conv3_1', 'conv4_1', 'conv5_1'], False, 1], ]),
    #('c55',[ ['', ['conv5_1', 'conv5_2'], False, 1], ]),
    #('c4455',[ ['', ['conv4_1', 'conv4_2', 'conv5_1', 'conv5_2'], False, 1], ]),
    #('c555',[ ['', ['conv5_1', 'conv5_2', 'conv5_3'], False, 1], ]),
    #('c444555',[ ['', ['conv4_1', 'conv4_2', 'conv4_3', 'conv5_1', 'conv5_2', 'conv5_3'], False, 1], ]),
  ]

  for model in ['vggface','vgg']:

    caffe, net, image_dims = setup_classifier(model=model,image_dims=image_dims)

    for tname,targets in targetset:

      psnr=[]
      ssim=[]
  
      for ipath1 in ipathset:
  
        for i in range(len(targets)):
          targets[i][0]=ipath1
        
        np.random.seed(123)
    
        root_dir = 'results_{}/{}/{}/{}'.format(int(round(t0)),model,tname,os.path.splitext(os.path.split(ipath1)[1])[0])
        if not os.path.exists(root_dir):
          os.makedirs(root_dir)
    
        # Generate activations for input images
        target_data_list = gen_target_data(root_dir, caffe, net, targets)
    
        # Generate white noise image
        init_img = np.random.normal(loc=0.5, scale=0.1, size=image_dims + (3,))
    
        solver_type = 'L-BFGS-B'
        solver_param = {}
    
        #test_all_gradients(init_img, net, all_target_blob_names, targets, target_data_list)
    
        A=caffe.io.load_image(ipath1)
        B=net.preprocess_inputs([A],auto_reshape=True)
        C=net.transformer.deprocess(net.inputs[0],B)
        D=caffe.io.resize_image(C,A.shape)
        print('input',A.shape,A.dtype,A.min(),A.max())
        print('pre',B.shape,B.dtype,B.min(),B.max())
        print('de',C.shape,C.dtype,C.min(),C.max())
        print('re',D.shape,D.dtype,D.min(),D.max())
    
        Chat=optimize_img(
          init_img, solver_type, solver_param, max_iter, display, root_dir, net,
          all_target_blob_names, targets, target_data_list
        )
        Dhat=caffe.io.resize_image(Chat,A.shape)
        print('best psnr = {:.4}, ssim = {:.4}'.format(measure.measure_PSNR(A,D,1).mean(),measure.measure_SSIM(A,D,1).mean()))
        print('actual psnr = {:.4}, ssim = {:.4}'.format(measure.measure_PSNR(A,Dhat,1).mean(),measure.measure_SSIM(A,Dhat,1).mean()))
        skimage.io.imsave('{}/eval_original.png'.format(root_dir),A)
        skimage.io.imsave('{}/eval_best.png'.format(root_dir),D)
        skimage.io.imsave('{}/eval_actual.png'.format(root_dir),Dhat)
        caption='psnr {:.4}, ssim {:.4}'.format(measure.measure_PSNR(A,Dhat,1).mean(),measure.measure_SSIM(A,Dhat,1).mean())
        subprocess.check_call('convert {root_dir}/eval_best.png {root_dir}/eval_actual.png -size {w}x -font Arial-Italic -pointsize 12 caption:{caption} -append {root_dir}/eval.png'.format(root_dir=pipes.quote(root_dir),caption=pipes.quote(caption),w=A.shape[1],h=A.shape[0]//10),shell=True)
        psnr.append(measure.measure_PSNR(A,Dhat,1).mean())
        ssim.append(measure.measure_SSIM(A,Dhat,1).mean())
  
      psnr=np.asarray(psnr).mean()
      ssim=np.asarray(ssim).mean()
      with open('results_{}/autoencoder.txt'.format(int(round(t0))),'a') as f:
        f.write('{},{},{},{}\n'.format(model,tname,psnr,ssim))

  t1=time.time()
  print('Finished in {} minutes.'.format((t1-t0)/60.0))

def read_lfw_attributes(ipath='dataset/lfw/lfw_attributes.txt'):
  # We verify that the first two attributes are person and sequence number
  # (from which the filename can be constructed).
  with open(ipath) as f:
    header=f.readline()
    attributes=f.readline().split('\t')[1:]
    assert attributes[0]=='person'
    assert attributes[1]=='imagenum'
    return header,attributes,[x.split('\t') for x in f.readlines()]

def lfw_filename(person,seq):
  person=person.replace(' ','_')
  return '{}/{}_{:04}.jpg'.format(person,person,int(seq))

def deepart_extract(ipath,prefix='data',model='vgg',blob_names=['conv3_1','conv4_1','conv5_1'],image_dims=(224,224)):
  # ipath = text file listing one image per line
  # model = vgg | vggface
  # blob_names = list of blobs to extract
  rlprint=ratelimit(interval=60)(print)

  caffe,net,image_dims=setup_classifier(model=model,image_dims=image_dims)
  h5f={}
  ds={}
    
  # minibatch processing
  M=10
  with open(ipath) as f:
    S=[x.strip() for x in f.readlines()]
  print('count =',len(S))
  work_units,work_done,work_t0=len(S),0,time.time()
  for i,x in enumerate(minibatch(S,M)):
    inputs=[os.path.join('images',y) for y in x]
    F=net.extract_features(inputs,blob_names,auto_reshape=True)
    for k,v in F.items():
      if i==0:
        print(k,v.shape,v.dtype,v.min(),v.max())
        h5f[k]=h5py.File('{}_{}.h5'.format(prefix,k),'w')
        ds[k]=h5f[k].create_dataset('DS',(len(S),)+v.shape[1:],chunks=(1,)+v.shape[1:],dtype='float32',compression='gzip',compression_opts=1)
        assert v.shape[0]==min(M,len(S))
      ds[k][i*M:i*M+v.shape[0]]=v[:]
    work_done=work_done+v.shape[0]
    rlprint('{}/{}, {} min remaining'.format(work_done,work_units,(work_units/work_done-1)*(time.time()-work_t0)/60.0))

  for k in h5f:
    h5f[k].close()

def deepart_extractlfw(model='vgg',blob_names=['conv3_1','conv4_1','conv5_1'],image_dims=(224,224)):
  # model = vgg | vggface
  # blob_names = list of blobs to extract
  rlprint=ratelimit(interval=60)(print)

  _,_,lfwattr=read_lfw_attributes()
  for x in lfwattr:
    ipath='images/lfw/{}'.format(lfw_filename(x[0],x[1]))
    assert os.path.exists(ipath)
  print('lfw count =',len(lfwattr))
  caffe,net,image_dims=setup_classifier(model=model,image_dims=image_dims)
  h5f={}
  ds={}
    
  # minibatch processing
  M=10
  work_units,work_done,work_t0=len(lfwattr),0,time.time()
  for i,x in enumerate(minibatch(lfwattr,M)):
    inputs=['images/lfw/'+lfw_filename(y[0],y[1]) for y in x]
    F=net.extract_features(inputs,blob_names,auto_reshape=True)
    for k,v in F.items():
      if i==0:
        print(k,v.shape,v.dtype,v.min(),v.max())
        h5f[k]=h5py.File('data_{}.h5'.format(k),'w')
        ds[k]=h5f[k].create_dataset('DS',(len(lfwattr),)+v.shape[1:],chunks=(1,)+v.shape[1:],dtype='float32',compression='gzip',compression_opts=1)
        assert v.shape[0]==M
      ds[k][i*M:i*M+v.shape[0]]=v[:]
    work_done=work_done+v.shape[0]
    rlprint('{}/{}, {} min remaining'.format(work_done,work_units,(work_units/work_done-1)*(time.time()-work_t0)/60.0))

  for k in h5f:
    h5f[k].close()

  # Example reading code:
  #h5f=h5py.File('data_conv3_1.h5','r')
  #a=h5f['DS'][0]
  #print(a.shape,a.dtype,a.min(),a.max()) # should be (256,56,56)
  #h5f.close()

def non_local_means(ipath,w,n,h,opath):
  a=skimage.io.imread(ipath)/255.0
  b=skimage.restoration.nl_means_denoising(a,w,n,h)
  skimage.io.imsave(opath,b)
  return b

def deepart_reconstruct(model='vgg',blob_names=['conv3_1','conv4_1','conv5_1'],blob_weights=[1,1,1],prefix='data',subsample=1,max_iter=2000,test_indices=None,data_indices=None,image_dims=(224,224),device_id=0,nlm=(3,21,0.03),hybrid_names=[],hybrid_weights=[],tv_lambda=0.001,tv_beta=2,gaussian_init=False,dataset='lfw',desc=''):
  # model = vgg | vggface
  # blob_names = list of blobs to match (must be in the right order, front to back)
  # blob_weights = cost function weight for each blob
  # prefix = target features will be read from PREFIX_BLOB.h5
  # subsample = process every N from the dataset
  # max_iter = number of iters to optimize (2000+ for good quality)
  # test_indices = list of dataset indices (corresponds to each entry in h5 files)
  # data_indices = list of h5 indices (for computing subsets of the data)
  #   Example: data_indices=[0,3], test_indices=[4,2] means compute with the first
  #   and fourth features in the h5 file and compare against the fifth and third
  #   images in the dataset.

  t0=time.time()

  # create network
  caffe,net,image_dims=setup_classifier(model=model,image_dims=image_dims,device_id=device_id)

  # init result dir
  root_dir='results_{}'.format(int(round(t0))) if desc=='' else 'results_{}_{}'.format(int(round(t0)),desc)
  if not os.path.exists(root_dir):
    os.makedirs(root_dir)
  def print(*args):
    with open('{}/log.txt'.format(root_dir),'a') as f:
      f.write(' '.join(str(x) for x in args)+'\n')
    sys.stdout.write(' '.join(str(x) for x in args)+'\n')
  print('root_dir',root_dir)
  print('model',model)
  print('blob_names',blob_names)
  print('blob_weights',blob_weights)
  print('hybrid_names',hybrid_names)
  print('hybrid_weights',hybrid_weights)
  print('prefix',prefix)
  print('subsample',subsample)
  print('max_iter',max_iter)
  print('image_dims',image_dims)
  print('tv_lambda',tv_lambda)
  print('tv_beta',tv_beta)
  print('gaussian_init',gaussian_init)
  print('dataset',dataset)
  rlprint=ratelimit(interval=60)(print)

  # read features
  h5f={}
  for k in blob_names:
    assert os.path.exists('{}_{}.h5'.format(prefix,k))
    h5f[k]=h5py.File('{}_{}.h5'.format(prefix,k),'r')
    print('h5f',k,h5f[k]['DS'].shape,h5f[k]['DS'].dtype)
    N=h5f[k]['DS'].shape[0]
  #_,_,lfwattr=read_lfw_attributes()
  with open('dataset/{}.txt'.format(dataset)) as f:
    original_names=[x.strip() for x in f.readlines()]
  if data_indices is None:
    # assume you want to process everything
    data_indices=list(range(N))
  else:
    # require that you specify the data -> dataset mapping
    assert not test_indices is None
    assert len(data_indices)==len(test_indices)
  if test_indices is None:
    test_indices=list(range(N))

  for x in hybrid_names:
    assert x not in blob_names
  assert len(hybrid_names)==len(hybrid_weights)

  # processing
  psnr=[]
  ssim=[]
  work_units,work_done,work_t0=len(test_indices),0,time.time()
  for j,i in enumerate(test_indices):
    if j % subsample: continue
    np.random.seed(123)

    #ipath='images/lfw/'+lfw_filename(lfwattr[i][0],lfwattr[i][1])
    ipath='images/'+original_names[i]
    #person=lfwattr[i][0]
    #seq=lfwattr[i][1]
    #basename=os.path.splitext(os.path.split(lfw_filename(person,seq))[1])[0]
    basename=os.path.splitext(os.path.split(ipath)[1])[0]

    # generate target list and target features
    all_target_blob_names=list(hybrid_names)+list(blob_names)
    targets=[]
    target_data_list=[]
    if len(hybrid_weights)>0:
      F=net.extract_features([ipath],hybrid_names,auto_reshape=True)
      for k,v in zip(hybrid_names,hybrid_weights):
        if len(targets)>0 and targets[-1][3]==v:
          targets[-1][1].append(k)
          target_data_list[-1][k]=F[k]
        else:
          targets.append((None,[k],False,v))
          target_data_list.append({k: F[k]})
        print('hybrid',k,v,F[k].shape,F[k].dtype)
    for k,v in zip(blob_names,blob_weights):
      if len(targets)>0 and targets[-1][3]==v:
        targets[-1][1].append(k)
        target_data_list[-1][k]=h5f[k]['DS'][data_indices[j]]
      else:
        targets.append((None,[k],False,v))
        target_data_list.append({k: h5f[k]['DS'][data_indices[j]]})
      print('target',k,v,h5f[k]['DS'][data_indices[j]].shape,h5f[k]['DS'][data_indices[j]].dtype)
    #target_data_list = gen_target_data(root_dir, caffe, net, targets)

    # Set initial value and reshape net
    if gaussian_init:
      init_img=np.random.normal(loc=0.5,scale=0.1,size=image_dims+(3,))
    else:
      init_img=caffe.io.load_image(ipath)
    deepart.set_data(net,init_img)
    #x0=np.ravel(init_img).astype(np.float64)
    x0=net.get_input_blob().ravel().astype(np.float64)
    bounds=zip(np.full_like(x0,-128),np.full_like(x0,162))
    solver_type='L-BFGS-B'
    solver_param={'maxiter': max_iter, 'iprint': -1}
    opt_res=scipy.optimize.minimize(deepart.objective_func,x0,args=(net,all_target_blob_names,targets,target_data_list,tv_lambda,tv_beta),bounds=bounds,method=solver_type,jac=True,options=solver_param)
    #print('opt_res',opt_res)
    #print('opt_res.x',opt_res.x.shape,opt_res.x.dtype)

    data=np.reshape(opt_res.x,net.get_input_blob().shape)[0]
    deproc_img=net.transformer.deprocess(net.inputs[0],data)
    A=caffe.io.load_image(ipath)
    B=np.clip(deproc_img,0,1)
    A=caffe.io.resize_image(A,B.shape)

    #print('A',A.shape,A.dtype,A.min(),A.max())
    #print('B',B.shape,B.dtype,B.min(),B.max())
    skimage.io.imsave('{}/{}-original.png'.format(root_dir,basename),A)
    skimage.io.imsave('{}/{}.png'.format(root_dir,basename),B)
    C=non_local_means('{}/{}.png'.format(root_dir,basename),3,21,0.04,'{}/{}-nlm.png'.format(root_dir,basename))
    caption='psnr {:.4}, ssim {:.4}'.format(measure.measure_PSNR(A,B,1).mean(),measure.measure_SSIM(A,B,1).mean())
    subprocess.check_call('convert {root_dir}/{basename}-original.png {root_dir}/{basename}.png -size {w}x -font Arial-Italic -pointsize 12 caption:{caption} -append {root_dir}/eval_{basename}.png'.format(root_dir=pipes.quote(root_dir),basename=pipes.quote(basename),ipath=pipes.quote(ipath),caption=pipes.quote(caption),w=A.shape[1],h=A.shape[0]//10),shell=True)
    psnr.append(measure.measure_PSNR(A,B,1).mean())
    ssim.append(measure.measure_SSIM(A,B,1).mean())
    with open('{}/results.txt'.format(root_dir),'a') as f:
      f.write('"{}",{},{},{}\n'.format(basename,i,psnr[-1],ssim[-1]))

    work_done=work_done+1*subsample
    rlprint('{}/{}, {} min remaining'.format(work_done,work_units,(work_units/work_done-1)*(time.time()-work_t0)/60.0))
  for k in h5f:
    h5f[k].close()

  print('psnr',psnr)
  print('ssim',ssim)
  psnr=np.asarray(psnr).mean()
  ssim=np.asarray(ssim).mean()
  with open('{}/results.txt'.format(root_dir),'a') as f:
    f.write(',{},{}\n'.format(psnr,ssim))

  t1=time.time()
  print('Finished in {} minutes.'.format((t1-t0)/60.0))

def deepart_pca(prefix='data',blob_names=['conv3_1','conv4_1','conv5_1']):
#def deepart_reconstruct(model='vgg',blob_names=['conv3_1','conv4_1','conv5_1'],blob_weights=[1,1,1],prefix='data',subsample=1,max_iter=2000,test_indices=None,data_indices=None,image_dims=(224,224),device_id=0,nlm=(3,21,0.03),hybrid_names=[],hybrid_weights=[],tv_lambda=0.001,tv_beta=2,gaussian_init=False,dataset='lfw',desc=''):
  t0=time.time()
  # read features
  h5f={}
  for k in blob_names:
    assert os.path.exists('{}_{}.h5'.format(prefix,k))
    h5f[k]=h5py.File('{}_{}.h5'.format(prefix,k),'r')
    print('h5f',k,h5f[k]['DS'].shape,h5f[k]['DS'].dtype)
    N=h5f[k]['DS'].shape[0]
  print('N',N)

  t1=time.time()
  print('Finished in {} minutes.'.format((t1-t0)/60.0))

def deepart_compare(inputs,name='compare'):
  t0=time.time()
  imshape=skimage.io.imread(glob.glob('{}/eval_*'.format(inputs[0]))[0]).shape
  inputs=[y for x in inputs for y in glob.glob('{}/eval_*'.format(x))]
  print(inputs)
  root_dir='results_{}'.format(int(round(t0)))
  if not os.path.exists(root_dir):
    os.makedirs(root_dir)
  subprocess.check_call('montage -geometry {}x{}+6+6 {} {}/{}.png'.format(imshape[1],imshape[0],' '.join(pipes.quote(x) for x in inputs),root_dir,name),shell=True)
  print('{}/{}.png'.format(root_dir,name))

if __name__ == '__main__':
  args=sys.argv[1:]

  #run_deepart(ipath1=args[0],ipath2=args[1],max_iter=int(args[2]))
  if args[0]=='identity':
    args=args[1:]
    image_dims=(125,125)
    params=('image_dims')
    params_desc={}
    args=filter_args(args,params,params_desc)
    deepart_identity(image_dims=image_dims)
  elif args[0]=='extractlfw':
    args=args[1:]
    model='vgg'
    image_dims=(125,125)
    params=('model','image_dims')
    params_desc={'model': 'vgg | vggface'}
    args=filter_args(args,params,params_desc)
    deepart_extractlfw(model=model,image_dims=image_dims)
  elif args[0]=='extract':
    args=args[1:]
    model='vgg'
    image_dims=(125,125)
    prefix='data'
    params=('model','image_dims','prefix')
    params_desc={'model': 'vgg | vggface'}
    args=filter_args(args,params,params_desc)
    deepart_extract(args[0],model=model,image_dims=image_dims,prefix=prefix)
  elif args[0]=='pca':
    deepart_pca()
  elif args[0]=='reconstruct':
    args=args[1:]
    model='vgg'
    test_indices=None
    data_indices=None
    subsample=1
    max_iter=3000
    image_dims=(125,125)
    prefix='data'
    nlm=(3,21,0.03)
    device_id=0
    hybrid_names=['conv1_1','conv2_1']
    hybrid_weights=[0.02,0.02]
    tv_lambda=0.001
    tv_beta=2
    gaussian_init=False
    dataset='lfw'
    desc=''
    params=('model','test_indices','data_indices','subsample','max_iter','image_dims','prefix','device_id','nlm','hybrid_names','hybrid_weights','tv_lambda','tv_beta','gaussian_init','dataset','desc')
    params_desc={'model': 'vgg | vggface','nlm': 'Non-local means parameters (window, distance, h_smooth_strength)', 'test_indices': 'which dataset images to compare against', 'data_indices': 'which entries in the h5 files to compute', 'hybrid_names': 'Must be in the same order as in the network', 'tv_lambda': 'Total variation loss weight', 'dataset': 'Original image filenames read from dataset/DATASET.txt', 'desc': 'Will be appended to the results directory name'}
    args=filter_args(args,params,params_desc)
    deepart_reconstruct(model=model,test_indices=test_indices,data_indices=data_indices,subsample=subsample,max_iter=max_iter,image_dims=image_dims,prefix=prefix,device_id=device_id,nlm=nlm,hybrid_names=hybrid_names,hybrid_weights=hybrid_weights,tv_lambda=tv_lambda,tv_beta=tv_beta,gaussian_init=gaussian_init,dataset=dataset,desc=desc)
  elif args[0]=='compare':
    args=args[1:]
    deepart_compare(inputs=args)
  else:
    raise ValueError('Unknown command')

