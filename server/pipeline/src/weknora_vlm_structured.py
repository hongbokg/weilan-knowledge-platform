"""Dedicated MinerU launcher preserving model-generated structural tokens.

llama-cpp-python 0.3.35 completion decoding defaults to special=False, stripping
OTSL cell/row markers before MinerU's table parser receives the response.
Keep this adapter scoped to this model/service; do not patch site-packages.
"""
import runpy
import llama_cpp

if llama_cpp.__version__!='0.3.35':
 raise RuntimeError('Revalidate structured-token adapter before changing llama-cpp-python version')

original_detokenize=llama_cpp.Llama.detokenize
def structured_detokenize(self,tokens,prev_tokens=None,special=True):
 return original_detokenize(self,tokens,prev_tokens=prev_tokens,special=special)

llama_cpp.Llama.detokenize=structured_detokenize
if __name__=='__main__':runpy.run_module('llama_cpp.server',run_name='__main__')
