package com.weilan.knowledge;
import android.content.Context;import android.graphics.*;import android.view.View;
/** Consistent vector stroke icons drawn natively, no bitmap or emoji UI controls. */
final class Icons extends View {
 private final String name;private final Paint paint=new Paint(3);private int color=0xff141414;
 Icons(Context c,String n){super(c);name=n;setContentDescription(n);}
 void color(int c){color=c;invalidate();}
 protected void onDraw(Canvas c){super.onDraw(c);c.save();float scale=Math.min(getWidth(),getHeight())/48f;c.translate((getWidth()-24*scale)/2,(getHeight()-24*scale)/2);c.scale(scale,scale);paint.setColor(color);paint.setStyle(Paint.Style.STROKE);paint.setStrokeWidth(1.8f);paint.setStrokeCap(Paint.Cap.ROUND);paint.setStrokeJoin(Paint.Join.ROUND);
  switch(name){
   case "shield":Path sh=new Path();sh.moveTo(12,1);sh.lineTo(22,5);sh.lineTo(21,14);sh.quadTo(19,21,12,24);sh.quadTo(5,21,3,14);sh.lineTo(2,5);sh.close();c.drawPath(sh,paint);line(c,7,12,11,16);line(c,11,16,17,9);break;
   case "menu":line(c,3,7,21,7);line(c,3,17,21,17);break;
   case "plus":c.drawCircle(12,12,9,paint);line(c,12,7,12,17);line(c,7,12,17,12);break;
   case "send":line(c,12,20,12,4);line(c,5,11,12,4);line(c,12,4,19,11);break;
   case "stop":c.drawRoundRect(5,5,19,19,2,2,paint);break;
   case "mic":c.drawRoundRect(9,2,15,15,3,3,paint);c.drawArc(5,6,19,20,0,180,false,paint);line(c,12,20,12,23);break;
   case "chat":c.drawRoundRect(2,3,22,19,6,6,paint);line(c,6,19,4,23);line(c,4,23,12,19);break;
   case "search":c.drawCircle(10,10,7,paint);line(c,15,15,22,22);break;
   case "clock":c.drawCircle(12,12,10,paint);line(c,12,5,12,12);line(c,12,12,17,12);break;
   case "folder":Path f=new Path();f.moveTo(2,6);f.lineTo(10,6);f.lineTo(12,9);f.lineTo(22,9);f.lineTo(22,21);f.lineTo(2,21);f.close();c.drawPath(f,paint);break;
   case "settings":c.drawCircle(12,12,4,paint);c.drawCircle(12,12,9,paint);for(int i=0;i<8;i++){double a=i*Math.PI/4;line(c,12+(float)Math.cos(a)*9,12+(float)Math.sin(a)*9,12+(float)Math.cos(a)*12,12+(float)Math.sin(a)*12);}break;
   case "attach":c.drawRoundRect(6,2,18,22,5,5,paint);line(c,10,7,10,17);break;
   case "back":line(c,18,12,4,12);line(c,4,12,10,6);line(c,4,12,10,18);break;
   case "speaker":line(c,3,9,7,9);line(c,7,9,13,4);line(c,13,4,13,20);line(c,13,20,7,15);line(c,7,15,3,15);line(c,3,15,3,9);c.drawArc(12,4,23,20,-65,130,false,paint);break;
   case "link":c.drawRoundRect(1,8,13,16,4,4,paint);c.drawRoundRect(11,8,23,16,4,4,paint);break;
   default:c.drawCircle(12,12,9,paint);break;
  }c.restore();
 }
 private void line(Canvas c,float x,float y,float a,float b){c.drawLine(x,y,a,b,paint);}
}
