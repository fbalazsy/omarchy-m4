// SPDX-License-Identifier: MIT
// J616 variant, lane SF 2026-10-21 (no counterpart in Kb2uka's tool; same in-process AudioDSP loading as
// his measure-*.c, Copyright (c) 2026 KB2UKA). Loads an AU from AudioDSP, optionally applies an aupreset
// (ClassInfo), and prints the parameter list with the AU's own names, ranges and current values.
// Renders nothing and plays nothing.
#include <AudioToolbox/AudioToolbox.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned fourcc(const char *s){return ((unsigned)s[0]<<24)|((unsigned)s[1]<<16)|((unsigned)s[2]<<8)|(unsigned)s[3];}
int main(int argc,char**argv){
  if(argc<3){fprintf(stderr,"usage: type subtype [preset]\n");return 1;}
  void*h=dlopen("/System/Library/Components/AudioDSP.component/Contents/MacOS/AudioDSP",RTLD_NOW|RTLD_LOCAL);
  if(!h)return 2; void(*reg)(void)=dlsym(h,"RegisterAudioUnits_Internal"); if(!reg)return 3; reg();
  AudioComponentDescription d={.componentType=fourcc(argv[1]),.componentSubType=fourcc(argv[2]),.componentManufacturer='appl'};
  AudioComponent c=AudioComponentFindNext(NULL,&d); if(!c){fprintf(stderr,"no component\n");return 4;}
  AudioComponentInstance au; OSStatus s=AudioComponentInstanceNew(c,&au); if(s){fprintf(stderr,"inst %d\n",(int)s);return 5;}
  if(argc>3){
    FILE*f=fopen(argv[3],"rb"); if(!f)return 6; fseek(f,0,SEEK_END); long n=ftell(f); fseek(f,0,SEEK_SET);
    unsigned char*raw=malloc(n); fread(raw,1,n,f); fclose(f);
    CFDataRef data=CFDataCreate(NULL,raw,n); CFPropertyListRef p=CFPropertyListCreateWithData(NULL,data,0,NULL,NULL);
    if(!p)return 7; s=AudioUnitSetProperty(au,kAudioUnitProperty_ClassInfo,kAudioUnitScope_Global,0,&p,sizeof(p));
    if(s){fprintf(stderr,"classinfo %d\n",(int)s);return 8;}
  }
  UInt32 sz=0; s=AudioUnitGetPropertyInfo(au,kAudioUnitProperty_ParameterList,kAudioUnitScope_Global,0,&sz,NULL);
  if(s){fprintf(stderr,"plist %d\n",(int)s);return 9;}
  AudioUnitParameterID*ids=malloc(sz); AudioUnitGetProperty(au,kAudioUnitProperty_ParameterList,kAudioUnitScope_Global,0,ids,&sz);
  unsigned cnt=sz/sizeof(*ids); printf("# %u params\n",cnt);
  for(unsigned i=0;i<cnt;i++){
    AudioUnitParameterInfo info; UInt32 isz=sizeof(info); memset(&info,0,sizeof info);
    s=AudioUnitGetProperty(au,kAudioUnitProperty_ParameterInfo,kAudioUnitScope_Global,ids[i],&info,&isz);
    AudioUnitParameterValue v=-9999; AudioUnitGetParameter(au,ids[i],kAudioUnitScope_Global,0,&v);
    char nm[256]="?"; if(!s){ if(info.cfNameString) CFStringGetCString(info.cfNameString,nm,sizeof nm,kCFStringEncodingUTF8); else strncpy(nm,info.name,sizeof nm);}
    printf("%u\t%s\tmin=%g max=%g def=%g unit=%u flags=0x%x\tvalue=%.9g\n",ids[i],nm,info.minValue,info.maxValue,info.defaultValue,(unsigned)info.unit,(unsigned)info.flags,v);
  }
  return 0;
}
