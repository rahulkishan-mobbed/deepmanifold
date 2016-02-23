#!/usr/bin/env python2

from __future__ import division
from __future__ import with_statement
from __future__ import print_function

import sys
import numpy as np
import scipy.optimize
import Queue
import threading
import minimize
import threadparallel

def witness_fn(r,x,P,Q,rbf_var,weight):
  # r is D dim
  # x is D dim
  # P is N x D, source distribution
  # Q is M x D, target distribution
  assert r.ndim==1
  assert x.ndim==1
  assert P.ndim==2
  assert Q.ndim==2
  #print('r',r.shape,r.dtype,r.min(),r.max())
  #print('x',x.shape,x.dtype,x.min(),x.max())
  #print('P',P.shape,P.dtype,P.min(),P.max())
  #print('Q',Q.shape,Q.dtype,Q.min(),Q.max())
  N=P.shape[0]
  M=Q.shape[0]
  x=x+r
  if N>0:
    xmP=x-P
    xmQ=x-Q
    #print('xmP',xmP.shape,xmP.dtype,xmP.min(),xmP.max())
    #print('xmQ',xmQ.shape,xmQ.dtype,xmQ.min(),xmQ.max())
    kxP=np.exp(-(xmP**2).sum(axis=1)/(2*rbf_var))
    kxQ=np.exp(-(xmQ**2).sum(axis=1)/(2*rbf_var))
    #print('kxP',kxP.shape,kxP.dtype,kxP.min(),kxP.max())
    #print('kxQ',kxQ.shape,kxQ.dtype,kxQ.min(),kxQ.max())
    assert kxP.shape==(N,)
    assert kxQ.shape==(M,)
    loss=kxP.sum()/N-kxQ.sum()/M+(r**2).sum()*weight
    grad=(-kxP.reshape(N,1)*xmP/N/rbf_var).sum(axis=0)+(kxQ.reshape(M,1)*xmQ/M/rbf_var).sum(axis=0)+2*r*weight
  else:
    # source set is empty
    xmQ=x-Q
    kxQ=np.exp(-(xmQ**2).sum(axis=1)/(2*rbf_var))
    assert kxQ.shape==(M,)
    loss=kxQ.sum()/M+(r**2).sum()*weight
    grad=(kxQ.reshape(M,1)*xmQ/M/rbf_var).sum(axis=0)+2*r*weight
  #print('loss',loss)
  #print('grad',grad.shape,grad.dtype,grad.min(),grad.max())
  #print('r',r.shape,r.dtype,r.min(),r.max(),np.linalg.norm(r))
  assert grad.shape==r.shape
  return loss,grad

def match_distribution(x,P,Q,weights,max_iter=5,rbf_var=1e4):
  print('match_distribution()')
  print('x',x.shape,x.dtype,x.min(),x.max())
  print('P',P.shape,P.dtype)
  print('Q',Q.shape,Q.dtype)
  print('weights',weights)

  # z score
  F=np.concatenate((P,Q),axis=0)
  print('F',F.shape,F.dtype,F.min(),F.max())
  sigma=F.std()
  loc=F.mean()
  print('sigma',sigma)
  print('loc',loc)
  assert sigma>0
  x=(x-loc)/sigma
  P=(P-loc)/sigma
  Q=(Q-loc)/sigma
  x_0=x*sigma+loc
  print('x',x.shape,x.dtype,x.min(),x.max())
  print('P',P.shape,P.dtype)
  print('Q',Q.shape,Q.dtype)
  print('x_0',x_0.shape,x_0.dtype,x_0.min(),x_0.max())
  
  x_result=[]
  r_result=[]

  checkgrad=True
  parallel=10
  for weight in weights:
    r=np.zeros_like(x)

    # SciPy optimizers don't work
    #solver_type='BFGS'
    #solver_type='CG'
    #print('solver_type',solver_type)
    ##solver_param={'maxiter': max_iter, 'iprint': -1, 'gtol': 1e-7}
    #solver_param={'gtol': 1e-5}
    #r_opt=scipy.optimize.minimize(witness_fn,r,args=(x,P,Q,rbf_var,weight),method=solver_type,jac=True,options=solver_param).x
    #r_opt=scipy.optimize.fmin_cg(witness_fn_loss,r,fprime=witness_fn_grad,args=(x,P,Q,rbf_var,weight))
    if checkgrad:
      def f(*args):
        return witness_fn(*args)[0]
      def g(*args):
        return witness_fn(*args)[1]
      print('Checking gradient ...')
      print(scipy.optimize.check_grad(f,g,r[:10],*(x[:10],P[:10,:10],Q[:10,:10],rbf_var,weight)))
    if parallel>1:
      assert (len(P) % parallel)==0
      assert (len(Q) % parallel)==0
      def witness_fn_parallel(r,x,P,Q,rbf_var,weight):
        result=threadparallel.unordered_parallel_call([witness_fn]*parallel,[(r,x,P[i*len(P)//parallel:(i+1)*len(P)//parallel],Q[i*len(Q)//parallel:(i+1)*len(Q)//parallel],rbf_var,weight) for i in range(parallel)],None)
        loss=sum(x[0] for x in result)
        grad=sum(x[1] for x in result)
        return loss,grad
      r_opt,loss_opt,iter_opt=minimize.minimize(r,witness_fn_parallel,(x,P,Q,rbf_var,weight,'rbf'),maxnumlinesearch=50,maxnumfuneval=None,red=1.0,verbose=True)
    else:
      r_opt,loss_opt,iter_opt=minimize.minimize(r,witness_fn,(x,P,Q,rbf_var,weight),maxnumlinesearch=50,maxnumfuneval=None,red=1.0,verbose=True)
    print('r_opt',r_opt.shape,r_opt.dtype,r_opt.min(),r_opt.max(),np.linalg.norm(r_opt))
    print(r_opt[:10])
    x_result.append((x+r_opt)*sigma+loc)
    r_result.append(r_opt*sigma)
  return x_0,np.asarray(x_result),np.asarray(r_result)

def witness_fn2(r,x,FFT,N,M,rbf_var,weight,verbose,checkrbf):
  # K is N+M+1
  # r is K dim, indicator vector
  # x is K dim, indicator vector
  # F is K x D, latent space vectors
  # FFT is F F^T
  K=N+M+1
  assert r.shape==(K,)
  assert x.shape==(K,)
  assert FFT.shape==(K,K)
  if verbose:
    print('r',r.shape,r.dtype,r.min(),r.max())
    print('x',x.shape,x.dtype,x.min(),x.max())

  P=np.eye(N,K)
  Q=np.concatenate([np.zeros((M,N)),np.eye(M,M+1)],axis=1)
  xpr=x+r
  xmP=xpr.reshape(1,K)-P # N x K
  xmQ=xpr.reshape(1,K)-Q # M x K
  AP=xmP.dot(FFT)
  AQ=xmQ.dot(FFT)
  Z=(-1.0/(2*rbf_var))
  eP=Z*(AP.dot(xmP.T)).sum(axis=1)
  eQ=Z*(AQ.dot(xmQ.T)).sum(axis=1)
  if checkrbf:
    print('rbf',eP.var(),eQ.var())
    if eP.mean()<-10 or eQ.mean()<-10:
      print('WARNING: rbf_var is too small (eP.mean()={}, eQ.mean={})'.format(eP.mean(),eQ.mean()))
  KP=np.exp(eP)
  KQ=np.exp(eQ)
  if checkrbf:
    print('KP',KP[:5],KP.mean(),KP.var())
    print('KQ',KQ[:5],KQ.mean(),KQ.var())
  B=FFT.dot(r)

  loss=(1.0/N)*KP.sum()-(1.0/M)*KQ.sum()+weight*(r.dot(B))
  grad=(1.0/N)*Z*(KP.reshape(N,1)*(AP.T.sum(axis=1).reshape(1,K)+N*AP)).sum(axis=0)-(1.0/M)*Z*(KQ.reshape(M,1)*(AQ.T.sum(axis=1).reshape(1,K)+M*AQ)).sum(axis=0)+2*weight*B

  if verbose:
    print('loss',loss)
    print('grad',grad.shape,grad.dtype,grad.min(),grad.max())
  assert grad.shape==r.shape
  return loss,grad

def manifold_traversal(F,N,M,weights,max_iter=5,rbf_var=1e4,verbose=True,checkgrad=True,checkrbf=True):
  # returns two arrays, xpr and r
  #   xpr is optimized x+r
  #   r is optimized r
  # multiply by F to get latent space vector
  if verbose:
    print('manifold_traversal()')
    print('F',F.shape,F.dtype,F.min(),F.max())
    print('N',N)
    print('M',M)
    print('weights',weights)

  xpr_result=[]
  r_result=[]
  r=np.zeros(len(F))
  x=np.zeros(len(F))
  x[-1]=1
  FFT=F.dot(F.T) # K x K
  for weight in weights:

    if checkgrad:
      def f(*args):
        return witness_fn2(*args)[0]
      def g(*args):
        return witness_fn2(*args)[1]
      print('Checking gradient ...')
      err=scipy.optimize.check_grad(f,g,r,*(x,FFT,N,M,rbf_var,weight,False,True))
      print('gradient error',err)
      assert err<1e-5

    r_opt,loss_opt,iter_opt=minimize.minimize(r,witness_fn2,(x,FFT,N,M,rbf_var,weight,verbose,checkrbf),maxnumlinesearch=50,maxnumfuneval=None,red=1.0,verbose=True)
    if verbose:
      print('r_opt',r_opt.shape,r_opt.dtype,r_opt.min(),r_opt.max(),np.linalg.norm(r_opt))
      print('r_opt values',r_opt[:5],'...',r_opt[N:N+5],'...',r_opt[-1])
    xpr_result.append(x+r_opt)
    r_result.append(r_opt)
    r=r_opt
  return np.asarray(xpr_result),np.asarray(r_result)

if __name__=='__main__':
  N=6
  M=6
  D=20
  P=np.random.random((N,D))+0.1
  Q=np.random.random((M,D))-0.1
  X=np.random.random((D,))
  F=np.concatenate([P,Q,X.reshape(1,D)])
  rbf_var=1e0
  weight=1e-3
  r=np.zeros(len(F))
  x=np.zeros(len(F))
  x[-1]=1
  FFT=F.dot(F.T) # K x K
  def f(*args):
    return witness_fn2(*args)[0]
  def g(*args):
    return witness_fn2(*args)[1]
  print('Checking gradient ...')
  err=scipy.optimize.check_grad(f,g,r,*(x,FFT,N,M,rbf_var,weight,False,True))
  print('gradient error',err)
  assert err<1e-5
  r_opt,loss_opt,iter_opt=minimize.minimize(r,witness_fn2,(x,FFT,N,M,rbf_var,weight,False,True),maxnumlinesearch=50,maxnumfuneval=None,red=1.0,verbose=True)
  print(r_opt[:N],r_opt[:N].var())
  print(r_opt[N:N+M],r_opt[N:N+M].var())
  print(r_opt[-1])

  # TODO test a multimodal Q
