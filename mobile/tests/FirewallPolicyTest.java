package com.weilan.knowledge;
import java.util.*;
public final class FirewallPolicyTest {
 static int passed=0;static void check(boolean result,String label){if(!result)throw new AssertionError(label);passed++;}
 public static void main(String[] args){String own="com.weilan.knowledge";
  List<String> clean=BlockPolicy.clean(Arrays.asList(own,"com.sample.ads","com.sample.ads",null,"","../unsafe","com.sample tracker"),own);
  check(clean.equals(Collections.singletonList("com.sample.ads")),"Exclude own, duplicate and invalid entries");
  try{BlockPolicy.requireNonEmpty(BlockPolicy.clean(Arrays.asList(own),own));throw new AssertionError("Empty capture list accepted");}catch(IllegalArgumentException expected){passed++;}
  try{BlockPolicy.requireNonEmpty(Collections.emptyList());throw new AssertionError("Empty policy accepted");}catch(IllegalArgumentException expected){passed++;}
  List<String> targets=BlockPolicy.clean(Arrays.asList("com.one.app","com.two.app"),own);BlockPolicy.requireNonEmpty(targets);
  check(targets.size()==2,"Preserve user selections");check(targets.get(0).equals("com.one.app"),"Stable order");
  check(BlockPolicy.clean(Arrays.asList("https://example.com","com.one.app\n"),own).isEmpty(),"Reject URL and control characters");
  System.out.println("PASS "+passed+" firewall policy safety checks");
 }
}
