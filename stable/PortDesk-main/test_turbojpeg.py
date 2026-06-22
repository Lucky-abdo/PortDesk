from turbojpeg import TurboJPEG, TJPF_BGR, TJSAMP_420
import numpy as np
import time
import cv2
import os

print("🔍 جاري اختبار TurboJPEG...")

try:
    jpeg = TurboJPEG()          # لو نجح = شغال
    print("✅ TurboJPEG() تم إنشاؤه بنجاح!")

    # اختبار encode + decode سريع
    # نعمل صورة وهمية (مثل اللي بيحصل في الـ streaming)
    test_img = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)

    start = time.perf_counter()
    jpeg_data = jpeg.encode(test_img, quality=75, jpeg_subsample=TJSAMP_420, pixel_format=TJPF_BGR)
    encode_time = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    decoded = jpeg.decode(jpeg_data)
    decode_time = (time.perf_counter() - start) * 1000

    print(f"✅ Encode نجح: {len(jpeg_data)/1024:.1f} KB في {encode_time:.1f} ms")
    print(f"✅ Decode  نجح: {decoded.shape} في {decode_time:.1f} ms")
    print(f"🎉 TurboJPEG شغال بكفاءة عالية!")

    # مقارنة بـ OpenCV (اختياري)
    if cv2 is not None:
        _, cv2_jpg = cv2.imencode('.jpg', test_img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        print(f"OpenCV encode: {len(cv2_jpg)/1024:.1f} KB")

except Exception as e:
    print("❌ فشل TurboJPEG:")
    print(e)
    print("\n💡 حلول شائعة:")
    print("   • على Windows: حمل libjpeg-turbo من https://github.com/libjpeg-turbo/libjpeg-turbo/releases")
    print("     وثبته (اختر vc64 أو gcc).")
    print("   • على Linux:   sudo apt install libturbojpeg-dev")
    print("   • على macOS:   brew install jpeg-turbo")
    print("   • بعد التثبيت: أعد تشغيل السيرفر.")