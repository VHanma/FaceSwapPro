package com.vaanhanma.omniscribe.engine;

import org.json.*;
import java.util.*;

public class TranscriptExporter {
    public static String txt(List<TranscriptSegment> s){
        StringBuilder b=new StringBuilder(); for(TranscriptSegment x:s) b.append(x.text).append('\n'); return b.toString();
    }
    public static String srt(List<TranscriptSegment> s){
        StringBuilder b=new StringBuilder(); int i=1;
        for(TranscriptSegment x:s){ b.append(i++).append("\n").append(time(x.startMs,',')).append(" --> ").append(time(x.endMs,',')).append("\n").append(x.text).append("\n\n"); }
        return b.toString();
    }
    public static String vtt(List<TranscriptSegment> s){
        StringBuilder b=new StringBuilder("WEBVTT\n\n");
        for(TranscriptSegment x:s){ b.append(time(x.startMs,'.')).append(" --> ").append(time(x.endMs,'.')).append("\n").append(x.text).append("\n\n"); }
        return b.toString();
    }
    public static String json(List<TranscriptSegment> s){
        JSONArray a=new JSONArray();
        for(TranscriptSegment x:s){
            JSONObject o=new JSONObject();
            try{
                o.put("start_ms",x.startMs);
                o.put("end_ms",x.endMs);
                o.put("text",x.text);
                a.put(o);
            }catch(Exception ignored){}
        }
        try{return a.toString(2);}catch(Exception ignored){return a.toString();}
    }
    private static String time(long ms,char sep){ long h=ms/3600000; ms%=3600000; long m=ms/60000; ms%=60000; long s=ms/1000; long z=ms%1000; return String.format(Locale.US,"%02d:%02d:%02d%c%03d",h,m,s,sep,z); }
}
