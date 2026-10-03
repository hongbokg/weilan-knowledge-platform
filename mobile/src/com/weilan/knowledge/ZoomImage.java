package com.weilan.knowledge;
import android.content.Context;import android.graphics.*;import android.view.*;
final class ZoomImage extends View {
 private final Bitmap image;private final Paint paint=new Paint(3);private final Matrix matrix=new Matrix();
 private final ScaleGestureDetector pinch;private final GestureDetector gesture;private float zoom=1,minimum=1;
 ZoomImage(Context c,Bitmap bitmap){super(c);image=bitmap;setContentDescription("原图，支持双指缩放和拖动");setBackgroundColor(0xfff6f6f6);
  pinch=new ScaleGestureDetector(c,new ScaleGestureDetector.SimpleOnScaleGestureListener(){public boolean onScale(ScaleGestureDetector d){float next=Math.max(minimum,Math.min(minimum*12,zoom*d.getScaleFactor()));matrix.postScale(next/zoom,next/zoom,d.getFocusX(),d.getFocusY());zoom=next;invalidate();return true;}});
  gesture=new GestureDetector(c,new GestureDetector.SimpleOnGestureListener(){public boolean onDown(MotionEvent e){return true;}public boolean onScroll(MotionEvent a,MotionEvent b,float x,float y){if(!pinch.isInProgress()){matrix.postTranslate(-x,-y);invalidate();}return true;}public boolean onDoubleTap(MotionEvent e){fit();return true;}});
 }
 private void fit(){minimum=Math.min((float)getWidth()/image.getWidth(),(float)getHeight()/image.getHeight());zoom=minimum;matrix.reset();matrix.postScale(zoom,zoom);matrix.postTranslate((getWidth()-image.getWidth()*zoom)/2,(getHeight()-image.getHeight()*zoom)/2);invalidate();}
 protected void onSizeChanged(int w,int h,int oldw,int oldh){fit();}
 protected void onDraw(Canvas canvas){if(!image.isRecycled())canvas.drawBitmap(image,matrix,paint);}
 public boolean onTouchEvent(MotionEvent e){pinch.onTouchEvent(e);gesture.onTouchEvent(e);if(e.getAction()==MotionEvent.ACTION_UP)performClick();return true;}
 public boolean performClick(){super.performClick();return true;}
}
