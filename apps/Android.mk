LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)
LOCAL_MODULE := Gramophone
LOCAL_MODULE_CLASS := APPS
LOCAL_MODULE_TAGS := optional
LOCAL_CERTIFICATE := PRESIGNED
LOCAL_SRC_FILES := gramophone/Gramophone.apk
LOCAL_PRODUCT_MODULE := true
LOCAL_OPTIONAL_USES_LIBRARIES := androidx.window.extensions androidx.window.sidecar
include $(BUILD_PREBUILT)

include $(CLEAR_VARS)
LOCAL_MODULE := QPGallery
LOCAL_MODULE_CLASS := APPS
LOCAL_MODULE_TAGS := optional
LOCAL_REPLACE_PREBUILT_APK_INSTALLED := $(LOCAL_PATH)/qpgallery/qpgallery.apk
LOCAL_CERTIFICATE := PRESIGNED
LOCAL_SRC_FILES := qpgallery/qpgallery.apk
LOCAL_PRODUCT_MODULE := true
LOCAL_OPTIONAL_USES_LIBRARIES := com.sec.android.app.multiwindow
LOCAL_OVERRIDES_PACKAGES := Gallery Gallery2 SnapdragonGallery
include $(BUILD_PREBUILT)
