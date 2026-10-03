package com.weilan.knowledge;
import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.security.KeyStore;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
/** Device-bound encrypted configuration. No plaintext keys, tokens or chat records on disk. */
final class SecureStore implements ApiSettings {
 private final android.content.SharedPreferences prefs;
 private final javax.crypto.SecretKey key;
 SecureStore(Context c) throws Exception {
  prefs=c.getSharedPreferences("native_secure",Context.MODE_PRIVATE);
  KeyStore ks=KeyStore.getInstance("AndroidKeyStore");ks.load(null);
  String alias="weilan.native.v2";
  if(!ks.containsAlias(alias)) { KeyGenerator g=KeyGenerator.getInstance("AES","AndroidKeyStore");
   g.init(new KeyGenParameterSpec.Builder(alias,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes("GCM").setEncryptionPaddings("NoPadding").build());g.generateKey(); }
  key=(javax.crypto.SecretKey)ks.getKey(alias,null);
 }
 public synchronized String get(String name,String fallback) throws Exception {
  String raw=prefs.getString(name,null);if(raw==null)return fallback;
  byte[] packed=Base64.decode(raw,Base64.NO_WRAP);if(packed.length<28)throw new Exception("Encrypted data damaged");
  Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key,new GCMParameterSpec(128,java.util.Arrays.copyOfRange(packed,0,12)));
  cipher.updateAAD(name.getBytes("UTF-8"));return new String(cipher.doFinal(java.util.Arrays.copyOfRange(packed,12,packed.length)),"UTF-8");
 }
 synchronized void put(String name,String value) throws Exception {
  Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key);cipher.updateAAD(name.getBytes("UTF-8"));
  byte[] payload=cipher.doFinal(value.getBytes("UTF-8")),iv=cipher.getIV(),packed=new byte[iv.length+payload.length];
  System.arraycopy(iv,0,packed,0,iv.length);System.arraycopy(payload,0,packed,iv.length,payload.length);
  if(!prefs.edit().putString(name,Base64.encodeToString(packed,Base64.NO_WRAP)).commit())throw new Exception("Unable to save");
 }
 synchronized void remove(String name){prefs.edit().remove(name).commit();}
}
