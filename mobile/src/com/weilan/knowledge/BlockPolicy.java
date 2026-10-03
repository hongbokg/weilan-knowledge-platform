package com.weilan.knowledge;
import java.util.*;
/** Never produce an empty VPN capture list: Android would route every application. */
final class BlockPolicy {
 static List<String> clean(Collection<String> packages,String ownPackage){
  LinkedHashSet<String> result=new LinkedHashSet<>();
  for(String p:packages)if(p!=null&&p.matches("[A-Za-z0-9_]+(?:\\.[A-Za-z0-9_]+)+")&&!p.equals(ownPackage))result.add(p);
  return new ArrayList<>(result);
 }
 static void requireNonEmpty(List<String> packages){if(packages.isEmpty())throw new IllegalArgumentException("请至少选择一个要断网的应用。");}
}
