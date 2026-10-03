package com.weilan.knowledge;
import android.net.VpnService;import android.app.*;import android.os.*;import android.content.*;import android.content.pm.ServiceInfo;
import java.io.*;import java.util.*;import java.util.concurrent.atomic.AtomicLong;import org.json.JSONArray;
/** Local per-app sink: selected apps' IPv4/IPv6 packets are read and discarded.
 * There is NO remote gateway, packet forwarding, content logging or TLS interception. */
public final class FirewallService extends VpnService {
 public static volatile boolean running=false;
 public static volatile String status="未启用";
 public static volatile int blockedApps=0;
 public static final AtomicLong packets=new AtomicLong(),bytes=new AtomicLong();
 private ParcelFileDescriptor tunnel;private Thread worker;private volatile int epoch=0;
 private static final String CHANNEL="weilan_local_firewall";
 private static final int NOTIFICATION=701;
 @Override public int onStartCommand(Intent intent,int flags,int startId){
  if(running&&(intent==null||!"APPLY".equals(intent.getAction())))return START_NOT_STICKY;
  if(running){running=false;closeTunnel();}
  final int cycle=++epoch;
  try{
   startNotice();
   if(prepare(this)!=null)throw new Exception("VPN 授权已撤销，请重新启用。");
   SecureStore store=new SecureStore(this);JSONArray stored=new JSONArray(store.get("firewall_apps","[]"));ArrayList<String> requested=new ArrayList<>();for(int i=0;i<stored.length();i++)requested.add(stored.optString(i));
   List<String> packages=BlockPolicy.clean(requested,getPackageName());BlockPolicy.requireNonEmpty(packages);
   Builder builder=new Builder().setSession("蔚蓝本地防火墙").setMtu(1500).setBlocking(true)
    .addAddress("10.111.0.1",32).addRoute("0.0.0.0",0)
    .addAddress("fd71:111::1",128).addRoute("::",0)
    .addDnsServer("10.111.0.2").setConfigureIntent(openApp());
   int installed=0;
   for(String pkg:packages){try{builder.addAllowedApplication(pkg);installed++;}catch(android.content.pm.PackageManager.NameNotFoundException ignored){}}
   if(installed==0)throw new Exception("所选应用已卸载，请重新选择。"); // no default-all fallback
   tunnel=builder.establish();if(tunnel==null)throw new Exception("无法建立本地 VPN，请检查授权或其他 VPN。");
   final ParcelFileDescriptor descriptor=tunnel;
   running=true;blockedApps=installed;packets.set(0);bytes.set(0);status="正在阻断 "+installed+" 个应用联网";
   ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(NOTIFICATION,notice(status));
   worker=new Thread(()->{
    try(FileInputStream in=new FileInputStream(descriptor.getFileDescriptor())){
     byte[] packet=new byte[32767];int n;
     while(!Thread.currentThread().isInterrupted()&&(n=in.read(packet))!=-1){if(n>0){packets.incrementAndGet();bytes.addAndGet(n);}/* Discard. Do not transmit or persist packet content. */}
    }catch(Exception e){if(cycle==epoch&&running)status="防火墙连接中断，需重新启用";}
    finally {if(cycle==epoch){running=false;stopSelf(startId);}}
   },"weilan-local-packet-sink");worker.start();
  }catch(Exception e){running=false;status=e.getMessage()==null?"防火墙启动失败":e.getMessage();closeTunnel();stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();}
  return START_NOT_STICKY;
 }
 private PendingIntent openApp(){Intent open=new Intent(this,MainActivity.class).putExtra("open_firewall",true).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP|Intent.FLAG_ACTIVITY_CLEAR_TOP);return PendingIntent.getActivity(this,701,open,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);}
 private Notification notice(String message){return new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.ic_lock_lock).setContentTitle("蔚蓝本地防火墙").setContentText(message).setOngoing(true).setContentIntent(openApp()).setCategory(Notification.CATEGORY_SERVICE).build();}
 private void startNotice(){NotificationManager manager=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);manager.createNotificationChannel(new NotificationChannel(CHANNEL,"本地防火墙状态",NotificationManager.IMPORTANCE_LOW));Notification n=notice("正在建立本地防火墙…");if(Build.VERSION.SDK_INT>=34)startForeground(NOTIFICATION,n,ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);else startForeground(NOTIFICATION,n);}
 private synchronized void closeTunnel(){epoch++;if(tunnel!=null){try{tunnel.close();}catch(Exception ignored){}tunnel=null;}if(worker!=null){worker.interrupt();worker=null;}}
 @Override public void onRevoke(){running=false;status="VPN 授权已撤销，所选应用可能恢复联网";closeTunnel();stopSelf();super.onRevoke();}
 @Override public void onDestroy(){running=false;closeTunnel();stopForeground(STOP_FOREGROUND_REMOVE);if(!status.contains("失败")&&!status.contains("中断")&&!status.contains("撤销"))status="已停止";super.onDestroy();}
}
